"""Deterministic OpenRouter model routing (policy + priority + budget)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AiRoutingEventRow, JobRow, OpenRouterModelPreferenceRow, SettingsRow
from app.services.ai_budget import AiBudgetService
from app.services.ai_cost import estimate_conservative_job_cost_micro, estimate_request_cost_micro
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


@dataclass
class ModelCandidate:
    model_id: str
    tier: str  # free | paid | unknown
    priority: int
    preference_id: int | None = None
    estimated_cost_micro_usd: int | None = None
    context_length: int | None = None
    skip_reason: str | None = None


@dataclass
class RoutingResult:
    candidates: list[ModelCandidate] = field(default_factory=list)
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


def classify_openrouter_failure(exc: Exception) -> str:
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
        if "validation" in message:
            return FAILURE_VALIDATION
        return FAILURE_PROVIDER
    message = str(exc).lower()
    if "cancel" in message:
        return FAILURE_CANCELLED
    if "budget" in message:
        return FAILURE_BUDGET
    if "validation" in message:
        return FAILURE_VALIDATION
    return FAILURE_PROVIDER


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
        preferences: list[OpenRouterModelPreferenceRow] | None = None,
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

        ordered: list[OpenRouterModelPreferenceRow] = []
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

        seen: set[str] = set()
        candidates: list[ModelCandidate] = []
        budget_status = self.budget.status()
        remaining = budget_status.remaining_micro_usd
        bypass = self.budget.can_bypass(trigger_type=trigger_type)
        skipped_by_cost = False

        for pref in ordered:
            if pref.model_id in seen:
                continue
            seen.add(pref.model_id)

            meta = self.catalog.annotate_model(pref.model_id, batch_size=pol.batch_size)
            pricing_tier = meta.get("pricing_tier") or "unknown"
            # Prefer preference pool tier for free/paid routing, but honor unknown pricing.
            pool_tier = pref.tier
            info = self.catalog.get_model(pref.model_id)
            if info is None:
                # Catalog miss must not block a configured v0.1/v0.2 model.
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
                # Apply the same 1.25 safety multiplier when callers pass raw tokens.
                raw = estimate_request_cost_micro(
                    estimated_input_tokens=max(estimated_input_tokens, 1),
                    estimated_output_tokens=max(estimated_output_tokens, 1),
                    input_price_per_million=in_price,
                    output_price_per_million=out_price,
                )
                if raw is not None:
                    from decimal import Decimal, ROUND_HALF_UP

                    est = int(
                        (Decimal(raw) * Decimal("1.25")).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    )

            # Per-job cap: skip paid models that cannot fit.
            if effective_tier == "paid" and est is not None:
                cap = pol.maximum_cost_per_job_micro_usd
                if cap is not None and est > int(cap):
                    skipped_by_cost = True
                    continue

            # Monthly budget: skip paid models that cannot fit remaining.
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

            candidates.append(
                ModelCandidate(
                    model_id=pref.model_id,
                    tier=effective_tier if effective_tier != "unknown" else pool_tier,
                    priority=pref.priority,
                    preference_id=pref.id,
                    estimated_cost_micro_usd=est,
                    context_length=meta.get("context_length"),
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
        logger.info(
            "job=%s strategy=%s candidates=%s selected=%s blocked=%s",
            job_id,
            strategy,
            [c.model_id for c in candidates],
            candidates[0].model_id if candidates else None,
            blocked,
        )
        if candidates:
            self.record_event(
                job_id=job_id,
                event="selected",
                strategy=strategy,
                model_id=candidates[0].model_id,
                detail=f"candidates={[c.model_id for c in candidates]}",
            )
        elif blocked:
            self.record_event(
                job_id=job_id,
                event="blocked",
                strategy=strategy,
                failure_category=FAILURE_BUDGET if blocked == "blocked_by_cost_policy" else FAILURE_INCOMPATIBLE,
                detail=blocked,
            )
        return result

    def record_event(
        self,
        *,
        job_id: int | None,
        event: str,
        strategy: str | None = None,
        model_id: str | None = None,
        next_model_id: str | None = None,
        failure_category: str | None = None,
        detail: str | None = None,
    ) -> None:
        row = AiRoutingEventRow(
            job_id=job_id,
            event=event,
            strategy=strategy,
            model_id=model_id,
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
    ) -> None:
        logger.info(
            "job=%s model=%s failure=%s fallback=%s",
            job_id,
            model_id,
            failure_category,
            next_model_id,
        )
        self.record_event(
            job_id=job_id,
            event="fallback" if next_model_id else "blocked",
            strategy=strategy,
            model_id=model_id,
            next_model_id=next_model_id,
            failure_category=failure_category,
        )
