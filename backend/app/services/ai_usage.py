"""Write and query authoritative AI usage records."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError
from app.ai.models import AIResponse, CostSource, Message
from app.ai.providers.base import AIProvider
from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
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
        elif isinstance(message, Message):
            parts.append(message.content)
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


def _cost_usd_value(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


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
        provider_id: str = OPENROUTER_PROVIDER_ID,
        request_id: str | None = None,
        attempt_number: int | None = None,
    ) -> AiUsageRecordRow:
        info = self.catalog.get_model(provider_id, model_id) or self.catalog.get_model(
            provider_id, batch_base_model(model_id)
        )
        in_price = info.prompt_price_per_million if info else None
        out_price = info.completion_price_per_million if info else None
        pricing_tier = tier or (
            info.pricing_tier.value
            if info and hasattr(info.pricing_tier, "value")
            else (info.pricing_tier if info else "unknown")
        )
        breakdown = estimate_cost_micro_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price_per_million=in_price,
            output_price_per_million=out_price,
            actual_cost_usd=actual_cost_usd,
        )
        actual_micro = usd_to_micro(actual_cost_usd) if actual_cost_usd is not None else None
        # Token-based estimate from the request-time snapshot — never overwrite
        # this with billed actual, and never treat estimate as billed cost.
        if (
            breakdown.input_price_micro_usd_per_million is not None
            and breakdown.output_price_micro_usd_per_million is not None
        ):
            estimated = breakdown.input_cost_micro_usd + breakdown.output_cost_micro_usd
        else:
            estimated = None

        if actual_micro is not None:
            cost_source = CostSource.PROVIDER_ACTUAL.value
        elif breakdown.pricing_source in ("openrouter", "estimated") and breakdown.total_cost_micro_usd:
            # Calculated from token usage × request-time pricing snapshot.
            actual_micro = breakdown.total_cost_micro_usd
            cost_source = CostSource.CALCULATED_FROM_USAGE.value
        else:
            cost_source = CostSource.ESTIMATED.value if estimated is not None else None

        row = AiUsageRecordRow(
            job_id=job_id,
            operation_type=operation_type,
            trigger_type=trigger_type,
            provider_id=provider_id,
            model_id=model_id,
            request_id=request_id,
            attempt_number=attempt_number,
            tier=pricing_tier,
            status=status,
            failure_category=failure_category,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or (input_tokens + output_tokens),
            estimated_cost_micro_usd=estimated,
            actual_cost_micro_usd=actual_micro,
            cost_source=cost_source,
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
        result: ChatResult | AIResponse,
        *,
        model_id: str,
        operation_type: str,
        trigger_type: str = "manual",
        job_id: int | None = None,
        status: str = "success",
        failure_category: str | None = None,
        outcome: str | None = None,
        tier: str | None = None,
        provider_id: str = OPENROUTER_PROVIDER_ID,
        request_id: str | None = None,
        attempt_number: int | None = None,
    ) -> AiUsageRecordRow:
        if isinstance(result, AIResponse):
            return self.record(
                model_id=result.model_id or model_id,
                operation_type=operation_type,
                trigger_type=trigger_type,
                job_id=job_id,
                status=status,
                failure_category=failure_category,
                outcome=outcome,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
                total_tokens=result.total_tokens or 0,
                actual_cost_usd=_cost_usd_value(result.actual_cost_usd),
                latency_ms=result.latency_ms,
                tier=tier,
                provider_id=result.provider_id or provider_id,
                request_id=result.request_id or request_id,
                attempt_number=attempt_number,
            )
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
            provider_id=provider_id,
            request_id=request_id,
            attempt_number=attempt_number,
        )

    def set_translation_outcomes(self, job_id: int, outcome: str) -> None:
        """Apply a job-level outcome only to translation-related usage rows."""
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


class RecordingAIProvider:
    """Wrap an AIProvider to persist ai_usage_records without changing HTTP behavior."""

    def __init__(
        self,
        inner: AIProvider,
        usage: AiUsageService,
        *,
        job_id: int | None,
        trigger_type: str,
        default_operation: str = "translation",
        tier: str | None = None,
        attempt_number: int | None = None,
        provider_id: str | None = None,
    ) -> None:
        self._inner = inner
        self._usage = usage
        self._job_id = job_id
        self._trigger_type = trigger_type
        self._default_operation = default_operation
        self._tier = tier
        self._attempt_number = attempt_number
        self._provider_id = provider_id or getattr(inner, "provider_id", OPENROUTER_PROVIDER_ID)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def display_name(self) -> str:
        return getattr(self._inner, "display_name", self._provider_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def is_configured(self) -> bool:
        return self._inner.is_configured()

    def supports(self, capability: str) -> bool:
        return self._inner.supports(capability)

    async def chat_completion(self, *args: Any, **kwargs: Any) -> AIResponse:
        from app.services.model_router import classify_provider_failure

        messages = kwargs.get("messages")
        model_id = kwargs.get("model_id") or kwargs.get("model") or (args[0] if args else "unknown")
        # Normalize legacy `model=` kwarg used by OpenRouterClient wrappers.
        if "model" in kwargs and "model_id" not in kwargs:
            kwargs = {**kwargs, "model_id": kwargs.pop("model")}
            model_id = kwargs["model_id"]
        request_id = kwargs.get("request_id") or str(uuid.uuid4())
        kwargs["request_id"] = request_id
        operation = operation_from_messages(
            messages if isinstance(messages, list) else None,
            default=self._default_operation,
        )
        try:
            result = await self._inner.chat_completion(*args, **kwargs)
        except AIProviderError as exc:
            self._usage.record(
                model_id=str(model_id),
                operation_type=operation,
                trigger_type=self._trigger_type,
                job_id=self._job_id,
                status="failed",
                failure_category=classify_provider_failure(exc),
                outcome="technical_failure",
                tier=self._tier,
                provider_id=self._provider_id,
                request_id=request_id,
                attempt_number=self._attempt_number,
            )
            self._usage.db.commit()
            raise
        # Also catch legacy OpenRouterError if it bubbles through.
        except Exception as exc:
            from app.translation.openrouter.client import OpenRouterError

            if isinstance(exc, OpenRouterError):
                self._usage.record(
                    model_id=str(model_id),
                    operation_type=operation,
                    trigger_type=self._trigger_type,
                    job_id=self._job_id,
                    status="failed",
                    failure_category=classify_provider_failure(exc),
                    outcome="technical_failure",
                    tier=self._tier,
                    provider_id=self._provider_id,
                    request_id=request_id,
                    attempt_number=self._attempt_number,
                )
                self._usage.db.commit()
            raise

        if isinstance(result, AIResponse):
            self._usage.record_chat_result(
                result,
                model_id=str(model_id),
                operation_type=operation,
                trigger_type=self._trigger_type,
                job_id=self._job_id,
                status="success",
                tier=self._tier,
                provider_id=self._provider_id,
                request_id=result.request_id or request_id,
                attempt_number=self._attempt_number,
            )
        else:
            # ChatResult from a thinly wrapped OpenRouter client.
            self._usage.record_chat_result(
                result,
                model_id=str(model_id),
                operation_type=operation,
                trigger_type=self._trigger_type,
                job_id=self._job_id,
                status="success",
                tier=self._tier,
                provider_id=self._provider_id,
                request_id=request_id,
                attempt_number=self._attempt_number,
            )
        self._usage.db.commit()
        return result

    async def run_chat_batch(self, *args: Any, **kwargs: Any):
        results = await self._inner.run_chat_batch(*args, **kwargs)
        model_id = kwargs.get("model_id") or kwargs.get("model") or "unknown"
        if isinstance(results, dict):
            for result in results.values():
                req_id = getattr(result, "request_id", None) or str(uuid.uuid4())
                self._usage.record_chat_result(
                    result,
                    model_id=str(model_id),
                    operation_type="translation",
                    trigger_type=self._trigger_type,
                    job_id=self._job_id,
                    status="success",
                    tier=self._tier,
                    provider_id=self._provider_id,
                    request_id=req_id,
                    attempt_number=self._attempt_number,
                )
        self._usage.db.commit()
        return results


# Backward-compatible alias.
RecordingOpenRouterClient = RecordingAIProvider
