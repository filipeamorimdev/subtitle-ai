"""Write and query authoritative AI usage records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AiUsageRecordRow
from app.services.ai_cost import estimate_cost_micro_usd, usd_to_micro
from app.services.ai_ranking import TRANSLATION_RANKING_OPS
from app.services.model_catalog import ModelCatalogService
from app.translation.openrouter.client import ChatResult, batch_base_model


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def operation_from_messages(messages: list[dict] | None, *, default: str = "translation") -> str:
    if not messages:
        return default
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            parts.append(message["content"])
    joined = "\n".join(parts).lower()
    if "classify media into a franchise universe" in joined:
        return "glossary_universe"
    if "extract audiovisual glossary terms" in joined:
        return "glossary_extract"
    if "translate only these" in joined and "missing subtitle blocks" in joined:
        return "translation_repair"
    if "reply with exactly: ok" in joined or "ping" in joined:
        return "model_test"
    if "professional audiovisual subtitle translator" in joined:
        return "translation"
    return default


class AiUsageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.catalog = ModelCatalogService(db)

    def record(
        self,
        *,
        model_id: str,
        operation_type: str,
        trigger_type: str = "manual",
        job_id: int | None = None,
        status: str = "success",
        failure_category: str | None = None,
        outcome: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        actual_cost_usd: float | None = None,
        latency_ms: int | None = None,
        tier: str | None = None,
    ) -> AiUsageRecordRow:
        info = self.catalog.get_model(model_id) or self.catalog.get_model(batch_base_model(model_id))
        in_price = info.prompt_price_per_million if info else None
        out_price = info.completion_price_per_million if info else None
        pricing_tier = tier or (info.pricing_tier if info else "unknown")
        breakdown = estimate_cost_micro_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price_per_million=in_price,
            output_price_per_million=out_price,
            actual_cost_usd=actual_cost_usd,
        )
        actual_micro = usd_to_micro(actual_cost_usd) if actual_cost_usd is not None else None
        estimated = breakdown.total_cost_micro_usd if breakdown.pricing_source != "none" else None
        if actual_micro is None and breakdown.pricing_source == "openrouter":
            actual_micro = breakdown.total_cost_micro_usd

        row = AiUsageRecordRow(
            job_id=job_id,
            operation_type=operation_type,
            trigger_type=trigger_type,
            model_id=model_id,
            tier=pricing_tier,
            status=status,
            failure_category=failure_category,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or (input_tokens + output_tokens),
            estimated_cost_micro_usd=estimated,
            actual_cost_micro_usd=actual_micro,
            input_price_micro_usd_per_million=breakdown.input_price_micro_usd_per_million,
            output_price_micro_usd_per_million=breakdown.output_price_micro_usd_per_million,
            pricing_timestamp=breakdown.pricing_timestamp,
            pricing_source=breakdown.pricing_source,
            latency_ms=latency_ms,
            created_at=utcnow(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def record_chat_result(
        self,
        result: ChatResult,
        *,
        model_id: str,
        operation_type: str,
        trigger_type: str = "manual",
        job_id: int | None = None,
        status: str = "success",
        failure_category: str | None = None,
        outcome: str | None = None,
        tier: str | None = None,
    ) -> AiUsageRecordRow:
        return self.record(
            model_id=result.model or model_id,
            operation_type=operation_type,
            trigger_type=trigger_type,
            job_id=job_id,
            status=status,
            failure_category=failure_category,
            outcome=outcome,
            input_tokens=result.input_tokens or 0,
            output_tokens=result.output_tokens or 0,
            total_tokens=result.total_tokens or 0,
            actual_cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            tier=tier,
        )

    def set_translation_outcomes(self, job_id: int, outcome: str) -> None:
        """Apply a job-level outcome only to translation-related usage rows.

        Glossary and model_test rows keep their own outcomes and must never
        inherit translation perfect_success / repair / validation results.
        """
        from sqlalchemy import select

        rows = list(
            self.db.scalars(
                select(AiUsageRecordRow).where(
                    AiUsageRecordRow.job_id == job_id,
                    AiUsageRecordRow.operation_type.in_(TRANSLATION_RANKING_OPS),
                )
            ).all()
        )
        for row in rows:
            if row.outcome is None:
                row.outcome = outcome
                self.db.add(row)
        self.db.flush()

    def set_job_outcome(self, job_id: int, outcome: str) -> None:
        """Backward-compatible alias; only updates translation-related rows."""
        self.set_translation_outcomes(job_id, outcome)


class RecordingOpenRouterClient:
    """Wrap OpenRouterClient to persist ai_usage_records without changing HTTP behavior."""

    def __init__(
        self,
        inner: Any,
        usage: AiUsageService,
        *,
        job_id: int | None,
        trigger_type: str,
        default_operation: str = "translation",
        tier: str | None = None,
    ) -> None:
        self._inner = inner
        self._usage = usage
        self._job_id = job_id
        self._trigger_type = trigger_type
        self._default_operation = default_operation
        self._tier = tier

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def chat_completion(self, *args: Any, **kwargs: Any) -> ChatResult:
        from app.services.model_router import classify_openrouter_failure
        from app.translation.openrouter.client import OpenRouterError

        messages = kwargs.get("messages")
        model_id = kwargs.get("model") or (args[0] if args else "unknown")
        if args and not kwargs.get("model"):
            # signature is keyword-only mostly; keep fallback
            pass
        operation = operation_from_messages(messages, default=self._default_operation)
        try:
            result = await self._inner.chat_completion(*args, **kwargs)
        except OpenRouterError as exc:
            self._usage.record(
                model_id=str(model_id),
                operation_type=operation,
                trigger_type=self._trigger_type,
                job_id=self._job_id,
                status="failed",
                failure_category=classify_openrouter_failure(exc),
                outcome="technical_failure",
                tier=self._tier,
            )
            self._usage.db.commit()
            raise
        self._usage.record_chat_result(
            result,
            model_id=str(model_id),
            operation_type=operation,
            trigger_type=self._trigger_type,
            job_id=self._job_id,
            status="success",
            tier=self._tier,
        )
        self._usage.db.commit()
        return result

    async def run_chat_batch(self, *args: Any, **kwargs: Any):
        results = await self._inner.run_chat_batch(*args, **kwargs)
        model_id = kwargs.get("model") or "unknown"
        for result in results.values():
            self._usage.record_chat_result(
                result,
                model_id=str(model_id),
                operation_type="translation",
                trigger_type=self._trigger_type,
                job_id=self._job_id,
                status="success",
                tier=self._tier,
            )
        self._usage.db.commit()
        return results
