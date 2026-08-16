"""Deterministic provider-neutral model routing (policy + priority + budget)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError
from app.ai.models import AIModelCandidate
from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.core.logging import get_logger
from app.db.models import AiModelPreferenceRow, AiRoutingEventRow, JobRow, SettingsRow
from app.services.ai_budget import AiBudgetService
from app.services.ai_cost import (
    CONSERVATIVE_COST_MULTIPLIER,
    estimate_conservative_job_cost_micro,
    estimate_request_cost_micro,
)
from app.services.model_catalog import ModelCatalogService
from app.services.model_preferences import list_preferences

logger = get_logger("model_router")


class RoutingBlockedError(Exception):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


FAILURE_TIMEOUT = "timeout"
FAILURE_RATE_LIMIT = "rate_limit"
FAILURE_PROVIDER = "provider_error"
FAILURE_INVALID = "invalid_response"
FAILURE_VALIDATION = "validation_error"
FAILURE_BUDGET = "budget_blocked"
FAILURE_UNKNOWN_PRICING = "unknown_pricing"
FAILURE_INCOMPATIBLE = "incompatible"
FAILURE_CANCELLED = "cancelled"
FAILURE_CONTEXT = "context_overflow"
FAILURE_AUTH = "auth_error"

TECHNICAL_FAILURES = frozenset(
    {
        FAILURE_TIMEOUT,
        FAILURE_RATE_LIMIT,
        FAILURE_PROVIDER,
        FAILURE_INVALID,
        FAILURE_CONTEXT,
    }
)


@dataclass
class RoutingPolicy:
    strategy: str = "free_first"
    allow_paid_fallback: bool = False
    allow_free_fallback: bool = True
    allow_unknown_pricing: bool = False
    maximum_cost_per_job_micro_usd: int | None = None
    batch_size: int = 25


# Backward-compatible alias.
ModelCandidate = AIModelCandidate


@dataclass
class RoutingResult:
    candidates: list[AIModelCandidate] = field(default_factory=list)
    blocked_reason: str | None = None
    strategy: str = "free_first"


def policy_from_settings(row: SettingsRow) -> RoutingPolicy:
    return RoutingPolicy(
        strategy=getattr(row, "routing_strategy", None) or "free_first",
        allow_paid_fallback=bool(getattr(row, "allow_paid_fallback", False)),
        allow_free_fallback=bool(getattr(row, "allow_free_fallback", True)),
        allow_unknown_pricing=bool(getattr(row, "allow_unknown_pricing", False)),
        maximum_cost_per_job_micro_usd=getattr(row, "maximum_cost_per_job_micro_usd", None),
        batch_size=int(getattr(row, "batch_size", 25) or 25),
    )


def classify_provider_failure(exc: Exception) -> str:
    """Map provider/translation exceptions to router failure categories."""
    if isinstance(exc, AIProviderError):
        category = getattr(exc, "category", None) or FAILURE_PROVIDER
        if category in TECHNICAL_FAILURES or category in (
            FAILURE_AUTH,
            FAILURE_VALIDATION,
            FAILURE_BUDGET,
            FAILURE_UNKNOWN_PRICING,
            FAILURE_INCOMPATIBLE,
            FAILURE_CANCELLED,
        ):
            return category
        # Normalize known aliases.
        if category == "auth_error":
            return FAILURE_AUTH
        return FAILURE_PROVIDER

    # Legacy OpenRouterError (HTTP client) — keep mapping for transitional paths.
    try:
        from app.translation.openrouter.client import OpenRouterError

        if isinstance(exc, OpenRouterError):
            status = getattr(exc, "status_code", None)
            message = str(exc).lower()
            if status == 429 or "rate limited" in message:
                return FAILURE_RATE_LIMIT
            if status == 408 or "timed out" in message or "timeout" in message:
                return FAILURE_TIMEOUT
            if status is not None and status >= 500:
                return FAILURE_PROVIDER
            if "connection" in message:
                return FAILURE_TIMEOUT
            if "malformed" in message or "invalid" in message:
                return FAILURE_INVALID
            if "validation" in message:
                return FAILURE_VALIDATION
            if "context" in message:
                return FAILURE_CONTEXT
            if getattr(exc, "retryable", False):
                return FAILURE_PROVIDER
            return FAILURE_PROVIDER
    except ImportError:
        pass

    message = str(exc).lower()
    if "cancel" in message:
        return FAILURE_CANCELLED
    if "budget" in message:
        return FAILURE_BUDGET
    if "validation" in message:
        return FAILURE_VALIDATION
    return FAILURE_PROVIDER


# Backward-compatible alias used by existing tests/call sites.
classify_openrouter_failure = classify_provider_failure


def is_technical_failure(category: str) -> bool:
    return category in TECHNICAL_FAILURES


class ModelRouter:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.catalog = ModelCatalogService(db)
        self.budget = AiBudgetService(db)

    def select_models(
        self,
        *,
        job: JobRow | None = None,
        policy: RoutingPolicy | None = None,
        preferences: list[AiModelPreferenceRow] | None = None,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        trigger_type: str = "manual",
        char_count: int | None = None,
    ) -> RoutingResult:
        settings = self.db.get(SettingsRow, 1)
        if settings is None:
            return RoutingResult(blocked_reason="no_settings")

        pol = policy or policy_from_settings(settings)
        prefs = preferences if preferences is not None else list_preferences(self.db, enabled_only=True)
        free = sorted([p for p in prefs if p.tier == "free" and p.enabled], key=lambda p: p.priority)
        paid = sorted([p for p in prefs if p.tier == "paid" and p.enabled], key=lambda p: p.priority)

        ordered: list[AiModelPreferenceRow] = []
        strategy = pol.strategy
        if strategy == "free_only":
            ordered = free
        elif strategy == "paid_only":
            ordered = paid
        elif strategy == "paid_first":
            ordered = list(paid)
            if pol.allow_free_fallback:
                ordered.extend(free)
        else:  # free_first (default)
            ordered = list(free)
            if pol.allow_paid_fallback:
                ordered.extend(paid)

        seen: set[tuple[str, str]] = set()
        candidates: list[AIModelCandidate] = []
        budget_status = self.budget.status()
        remaining = budget_status.remaining_micro_usd
        bypass = self.budget.can_bypass(trigger_type=trigger_type)
        skipped_by_cost = False

        for pref in ordered:
            provider_id = getattr(pref, "provider_id", None) or OPENROUTER_PROVIDER_ID
            identity = (provider_id, pref.model_id)
            if identity in seen:
                continue
            seen.add(identity)

            meta = self.catalog.annotate_model(
                pref.model_id, batch_size=pol.batch_size, provider_id=provider_id
            )
            pricing_tier = meta.get("pricing_tier") or "unknown"
            pool_tier = pref.tier
            info = self.catalog.get_model(provider_id, pref.model_id)

            # Skip models past hard sunset (warn-only for announced deprecation).
            if info is not None and info.is_past_sunset():
                logger.info(
                    "provider=%s model=%s skipped past_sunset replacement=%s",
                    provider_id,
                    pref.model_id,
                    info.replacement_model_id,
                )
                continue

            if info is None:
                effective_tier = pool_tier
            elif pricing_tier == "unknown":
                effective_tier = "unknown"
            else:
                effective_tier = pool_tier

            if meta.get("unavailable"):
                continue
            if not meta.get("compatible", True):
                continue
            if effective_tier == "unknown" and not pol.allow_unknown_pricing:
                continue

            in_price = info.prompt_price_per_million if info else None
            out_price = info.completion_price_per_million if info else None
            est = None
            if char_count is not None:
                cons = estimate_conservative_job_cost_micro(
                    char_count=char_count,
                    input_price_per_million=in_price,
                    output_price_per_million=out_price,
                )
                est = cons.conservative_cost_micro_usd
            elif estimated_input_tokens or estimated_output_tokens:
                raw = estimate_request_cost_micro(
                    estimated_input_tokens=max(estimated_input_tokens, 1),
                    estimated_output_tokens=max(estimated_output_tokens, 1),
                    input_price_per_million=in_price,
                    output_price_per_million=out_price,
                )
                if raw is not None:
                    from decimal import Decimal, ROUND_HALF_UP

                    est = int(
                        (Decimal(raw) * CONSERVATIVE_COST_MULTIPLIER).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    )

            if effective_tier == "paid" and est is not None:
                cap = pol.maximum_cost_per_job_micro_usd
                if cap is not None and est > int(cap):
                    skipped_by_cost = True
                    continue

            if (
                effective_tier == "paid"
                and budget_status.enabled
                and not bypass
                and remaining is not None
                and est is not None
                and est > remaining
            ):
                skipped_by_cost = True
                continue

            caps = set(meta.get("capabilities") or [])
            if info is not None:
                caps = set(info.capabilities)

            candidates.append(
                AIModelCandidate(
                    provider_id=provider_id,
                    model_id=pref.model_id,
                    tier=effective_tier if effective_tier != "unknown" else pool_tier,
                    priority=pref.priority,
                    preference_id=pref.id,
                    estimated_cost_micro_usd=est,
                    context_length=meta.get("context_length"),
                    capabilities=caps,
                )
            )

        blocked = None
        if not candidates:
            if skipped_by_cost:
                blocked = "blocked_by_cost_policy"
            else:
                blocked = "no_compatible_model"

        result = RoutingResult(candidates=candidates, blocked_reason=blocked, strategy=strategy)
        job_id = job.id if job else None
        selected = candidates[0] if candidates else None
        logger.info(
            "job=%s strategy=%s candidates=%s selected_provider=%s selected_model=%s blocked=%s",
            job_id,
            strategy,
            [f"{c.provider_id}/{c.model_id}" for c in candidates],
            selected.provider_id if selected else None,
            selected.model_id if selected else None,
            blocked,
        )
        if selected:
            self.record_event(
                job_id=job_id,
                event="selected",
                strategy=strategy,
                provider_id=selected.provider_id,
                model_id=selected.model_id,
                detail=f"candidates={[f'{c.provider_id}/{c.model_id}' for c in candidates]}",
            )
        elif blocked:
            self.record_event(
                job_id=job_id,
                event="blocked",
                strategy=strategy,
                failure_category=FAILURE_BUDGET
                if blocked == "blocked_by_cost_policy"
                else FAILURE_INCOMPATIBLE,
                detail=blocked,
            )
        return result

    def record_event(
        self,
        *,
        job_id: int | None,
        event: str,
        strategy: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        next_provider_id: str | None = None,
        next_model_id: str | None = None,
        failure_category: str | None = None,
        detail: str | None = None,
    ) -> None:
        row = AiRoutingEventRow(
            job_id=job_id,
            event=event,
            strategy=strategy,
            provider_id=provider_id or (OPENROUTER_PROVIDER_ID if model_id else None),
            model_id=model_id,
            next_provider_id=next_provider_id
            or (OPENROUTER_PROVIDER_ID if next_model_id else None),
            next_model_id=next_model_id,
            failure_category=failure_category,
            detail=(detail or "")[:512] or None,
        )
        self.db.add(row)
        self.db.flush()

    def record_fallback(
        self,
        *,
        job_id: int | None,
        model_id: str,
        next_model_id: str | None,
        failure_category: str,
        strategy: str | None = None,
        provider_id: str = OPENROUTER_PROVIDER_ID,
        next_provider_id: str | None = None,
    ) -> None:
        next_pid = next_provider_id or (provider_id if next_model_id else None)
        logger.info(
            "job=%s provider=%s model=%s failure=%s next_provider=%s next_model=%s",
            job_id,
            provider_id,
            model_id,
            failure_category,
            next_pid,
            next_model_id,
        )
        self.record_event(
            job_id=job_id,
            event="fallback" if next_model_id else "blocked",
            strategy=strategy,
            provider_id=provider_id,
            model_id=model_id,
            next_provider_id=next_pid,
            next_model_id=next_model_id,
            failure_category=failure_category,
        )
