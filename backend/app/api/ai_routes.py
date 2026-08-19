"""AI model routing, catalog, usage, budget, and provider APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.bootstrap import bootstrap_providers
from app.ai.credentials import ProviderAccountService
from app.ai.errors import AIProviderError
from app.ai.models import PricingTier
from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.ai.providers.registry import get_provider_registry
from app.api.schemas import (
    AiBudgetOut,
    AiModelPatch,
    AiModelPreferenceIn,
    AiModelReorderIn,
    AiModelTestIn,
    AiRoutingOut,
    AiRoutingUpdate,
    ConnectionTestResult,
    SettingsUpdate,
)
from app.db import get_db
from app.services.ai_budget import AiBudgetService, BudgetBlockedError
from app.services.ai_cost import estimate_request_cost_micro, micro_to_usd
from app.services.ai_ranking import AiRankingService
from app.services.ai_stats import AiStatsService, period_bounds
from app.services.ai_usage import AiUsageService
from app.services.model_catalog import ModelCatalogService, check_compatibility
from app.services.model_preferences import ModelPreferenceService
from app.services.settings import SettingsService
from app.translation.openrouter.client import OpenRouterError, batch_base_model

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ProviderUpdateIn(BaseModel):
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None
    clear_base_url: bool = False
    enabled: bool | None = None
    display_name: str | None = None
    openrouter_log_full_exchanges: bool | None = None
    openrouter_temperature: float | None = Field(default=None, ge=0, le=2)


def _routing_out(db: Session) -> AiRoutingOut:
    public = SettingsService(db).get_public()
    return AiRoutingOut(
        routing_strategy=public.routing_strategy,
        allow_paid_fallback=public.allow_paid_fallback,
        allow_free_fallback=public.allow_free_fallback,
        allow_unknown_pricing=public.allow_unknown_pricing,
        maximum_cost_per_job_usd=public.maximum_cost_per_job_usd,
        monthly_budget_enabled=public.monthly_budget_enabled,
        monthly_budget_amount_usd=public.monthly_budget_amount_usd,
        allow_manual_budget_override=public.allow_manual_budget_override,
        openrouter_log_full_exchanges=public.openrouter_log_full_exchanges,
        openrouter_temperature=public.openrouter_temperature,
    )


def _tier_value(tier: Any) -> str:
    if isinstance(tier, PricingTier):
        return tier.value
    return str(tier or "unknown")


def _provider_display(provider_id: str) -> str:
    if provider_id == OPENROUTER_PROVIDER_ID:
        return "OpenRouter"
    return provider_id.title()


@router.get("/overview")
def ai_overview(
    period: str = Query(default="month"),
    db: Session = Depends(get_db),
) -> dict:
    return AiStatsService(db).overview(period=period)


@router.get("/usage")
def ai_usage(
    period: str = Query(default="30d"),
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    tier: str | None = None,
    operation: str | None = None,
    trigger_type: str | None = None,
    status: str | None = None,
    failure: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = "cost_usd",
    db: Session = Depends(get_db),
) -> dict:
    return AiStatsService(db).usage_page(
        period=period,
        start=start,
        end=end,
        model=model,
        tier=tier,
        operation=operation,
        trigger_type=trigger_type,
        status=status,
        failure=failure,
        offset=offset,
        limit=limit,
        sort=sort,
        provider_id=provider_id,
    )


@router.get("/costs")
def ai_costs(
    period: str = Query(default="30d"),
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return AiStatsService(db).costs(period=period, start=start, end=end)


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)) -> dict:
    bootstrap_providers(db)
    accounts = ProviderAccountService(db)
    registry = get_provider_registry()
    items = []
    for provider in registry.enabled():
        public = accounts.get_public(provider.provider_id)
        items.append(
            {
                "provider_id": provider.provider_id,
                "display_name": provider.display_name,
                "enabled": public.enabled,
                "configured": public.configured,
                "api_key_masked": public.api_key_masked,
                "base_url": public.base_url,
                "status": "configured" if public.configured else "unavailable",
            }
        )
    return {"providers": items}


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: str,
    payload: ProviderUpdateIn,
    db: Session = Depends(get_db),
) -> dict:
    bootstrap_providers(db)
    if get_provider_registry().get_optional(provider_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    accounts = ProviderAccountService(db)
    public = accounts.set_credentials(
        provider_id,
        api_key=payload.api_key,
        clear_api_key=payload.clear_api_key,
        base_url=payload.base_url,
        clear_base_url=payload.clear_base_url,
        enabled=payload.enabled,
        display_name=payload.display_name,
    )
    if (
        (payload.openrouter_log_full_exchanges is not None or payload.openrouter_temperature is not None)
        and provider_id == OPENROUTER_PROVIDER_ID
    ):
        SettingsService(db).update(
            SettingsUpdate(
                openrouter_log_full_exchanges=payload.openrouter_log_full_exchanges,
                openrouter_temperature=payload.openrouter_temperature,
            )
        )
    routing = _routing_out(db)
    return {
        "provider_id": public.provider_id,
        "display_name": public.display_name,
        "enabled": public.enabled,
        "configured": public.configured,
        "api_key_masked": public.api_key_masked,
        "base_url": public.base_url,
        "openrouter_log_full_exchanges": routing.openrouter_log_full_exchanges,
        "openrouter_temperature": routing.openrouter_temperature,
    }


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, db: Session = Depends(get_db)) -> dict:
    bootstrap_providers(db)
    if get_provider_registry().get_optional(provider_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    public = ProviderAccountService(db).clear_credentials(provider_id)
    return {
        "ok": True,
        "provider_id": public.provider_id,
        "configured": public.configured,
    }


@router.post("/providers/{provider_id}/test", response_model=ConnectionTestResult)
async def test_provider(
    provider_id: str,
    fresh: bool = Query(default=False),
    model_id: str | None = None,
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    bootstrap_providers(db)
    provider = get_provider_registry().get_optional(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
    try:
        if hasattr(provider, "test_connection"):
            # OpenRouterProvider supports force=
            try:
                health = await provider.test_connection(model_id, force=fresh)  # type: ignore[call-arg]
            except TypeError:
                health = await provider.test_connection(model_id)
        else:
            raise AIProviderError("Provider does not support connection tests")
    except AIProviderError as exc:
        return ConnectionTestResult(ok=False, message=str(exc))
    ok = health.status.value == "connected"
    return ConnectionTestResult(
        ok=ok,
        message=health.message or health.status.value,
        details={
            "status": health.status.value,
            "configured": health.configured,
            "cached": health.cached,
            "provider_id": health.provider_id,
            "model_id": health.model_id,
            "tested_at": health.tested_at.isoformat() if health.tested_at else None,
        },
    )


@router.get("/models")
async def ai_models(db: Session = Depends(get_db)) -> dict:
    bootstrap_providers(db)
    settings = SettingsService(db)
    public = settings.get_public()
    accounts = ProviderAccountService(db)
    account = accounts.get_public(OPENROUTER_PROVIDER_ID)
    catalog = ModelCatalogService(db)
    snapshot = catalog.get_cached(OPENROUTER_PROVIDER_ID)
    if snapshot is None:
        try:
            snapshot = await catalog.get_models(provider_id=OPENROUTER_PROVIDER_ID)
        except (AIProviderError, OpenRouterError):
            # Catalog may re-raise legacy OpenRouterError when no cache exists;
            # the models page should still render preferences/routing.
            snapshot = catalog.get_cached(OPENROUTER_PROVIDER_ID)
    prefs = ModelPreferenceService(db).list_all()
    start, end, _, _ = period_bounds("30d")
    ranking_rows = AiRankingService(db).rank_models(start=start, end=end)
    ranking = {(r.provider_id, r.model_id): r for r in ranking_rows}
    ranking_by_model = {r.model_id: r for r in ranking_rows}
    catalog_models = []
    if snapshot:
        for model in snapshot.models:
            compatible, reason = check_compatibility(model, batch_size=public.batch_size)
            catalog_models.append(
                {
                    "provider_id": model.provider_id,
                    "provider_name": _provider_display(model.provider_id),
                    "id": model.model_id,
                    "model_id": model.model_id,
                    "name": model.name,
                    "description": model.description,
                    "prompt_price_per_million": model.prompt_price_per_million,
                    "completion_price_per_million": model.completion_price_per_million,
                    "context_length": model.context_length,
                    "pricing_tier": _tier_value(model.pricing_tier),
                    "compatible": compatible,
                    "compatibility_reason": reason,
                    "stale": snapshot.stale,
                    "pricing_freshness": snapshot.freshness.value,
                    "unavailable": not model.available,
                    "capabilities": sorted(model.capabilities),
                    "deprecated": model.deprecated,
                    "sunset_date": model.sunset_date.isoformat() if model.sunset_date else None,
                    "replacement_model_id": model.replacement_model_id,
                    "input_modalities": (model.metadata or {}).get("input_modalities"),
                    "output_modalities": (model.metadata or {}).get("output_modalities"),
                }
            )
    preferred = []
    for pref in prefs:
        provider_id = getattr(pref, "provider_id", None) or OPENROUTER_PROVIDER_ID
        meta = catalog.annotate_model(
            pref.model_id, batch_size=public.batch_size, provider_id=provider_id
        )
        rank = ranking.get((provider_id, pref.model_id)) or ranking.get(
            (provider_id, batch_base_model(pref.model_id))
        ) or ranking_by_model.get(pref.model_id)
        preferred.append(
            {
                "id": pref.id,
                "provider_id": provider_id,
                "provider_name": _provider_display(provider_id),
                "model_id": pref.model_id,
                "tier": pref.tier,
                "priority": pref.priority,
                "enabled": pref.enabled,
                **meta,
                "configured_priority": pref.priority,
                "adaptive_rank": rank.adaptive_rank if rank else None,
                "adaptive_score": rank.adaptive_score if rank else None,
                "confidence": rank.confidence if rank else "insufficient",
                "sample_count": rank.sample_count if rank else 0,
                "clean_success_rate": rank.clean_success_rate if rank else None,
                "repair_rate": rank.repair_rate if rank else None,
                "average_cost_per_clean_success_usd": (
                    rank.average_cost_per_clean_success_usd if rank else None
                ),
                "average_latency_ms": rank.average_latency_ms if rank else None,
                "last_used_at": rank.last_used_at.isoformat() if rank and rank.last_used_at else None,
            }
        )
    return {
        "openrouter_configured": account.configured,
        "openrouter_api_key_masked": account.api_key_masked,
        "catalog_fetched_at": snapshot.fetched_at.isoformat() if snapshot and snapshot.fetched_at else None,
        "catalog_stale": bool(snapshot.stale) if snapshot else True,
        "catalog_age_seconds": snapshot.age_seconds if snapshot else None,
        "pricing_freshness": snapshot.freshness.value if snapshot else "unknown",
        "preferences": preferred,
        "catalog": catalog_models,
        "routing": _routing_out(db).model_dump(),
    }


@router.post("/models/refresh")
async def refresh_models(
    provider_id: str = Query(default=OPENROUTER_PROVIDER_ID),
    db: Session = Depends(get_db),
) -> dict:
    bootstrap_providers(db)
    catalog = ModelCatalogService(db)
    try:
        snapshot = await catalog.get_models(provider_id=provider_id, force_refresh=True)
    except AIProviderError as exc:
        cached = catalog.get_cached(provider_id)
        if cached is None:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": False,
            "stale": True,
            "provider_id": provider_id,
            "message": str(exc),
            "fetched_at": cached.fetched_at.isoformat() if cached.fetched_at else None,
            "count": len(cached.models),
            "pricing_freshness": cached.freshness.value,
        }
    return {
        "ok": True,
        "stale": snapshot.stale,
        "provider_id": provider_id,
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        "count": len(snapshot.models),
        "pricing_freshness": snapshot.freshness.value,
    }


@router.post("/models/test", response_model=ConnectionTestResult)
async def test_model(payload: AiModelTestIn, db: Session = Depends(get_db)) -> ConnectionTestResult:
    bootstrap_providers(db)
    accounts = ProviderAccountService(db)
    if not accounts.is_configured(OPENROUTER_PROVIDER_ID):
        return ConnectionTestResult(ok=False, message="OpenRouter API key is not configured.")
    catalog = ModelCatalogService(db)
    provider_id = OPENROUTER_PROVIDER_ID
    info = catalog.get_model(provider_id, payload.model_id)
    tier = _tier_value(info.pricing_tier) if info else "unknown"
    budget = AiBudgetService(db)
    usage = AiUsageService(db)
    estimate = estimate_request_cost_micro(
        estimated_input_tokens=32,
        estimated_output_tokens=16,
        input_price_per_million=info.prompt_price_per_million if info else None,
        output_price_per_million=info.completion_price_per_million if info else None,
    )
    reservation = None
    try:
        reservation = budget.reserve(
            amount_micro_usd=int(estimate or 0),
            job_id=None,
            trigger_type="manual",
            tier=tier,
        )
        provider = get_provider_registry().get(provider_id)
        result = await provider.chat_completion(
            model_id=batch_base_model(payload.model_id),
            messages=[
                {"role": "system", "content": "Reply with exactly: ok"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
        )
        usage.record_chat_result(
            result,
            model_id=payload.model_id,
            operation_type="model_test",
            trigger_type="manual",
            status="success",
            tier=tier,
            provider_id=provider_id,
        )
        db.commit()
        return ConnectionTestResult(
            ok=True,
            message=f"Model {payload.model_id} responded.",
            details={
                "ok": True,
                "provider_id": provider_id,
                "model": result.model_id,
                "sample": (result.content or "")[:80],
            },
        )
    except BudgetBlockedError as exc:
        usage.record(
            model_id=payload.model_id,
            operation_type="model_test",
            trigger_type="manual",
            status="failed",
            failure_category="budget_blocked",
            outcome="budget_blocked",
            tier=tier,
            provider_id=provider_id,
        )
        db.commit()
        return ConnectionTestResult(ok=False, message=str(exc))
    except AIProviderError as exc:
        usage.record(
            model_id=payload.model_id,
            operation_type="model_test",
            trigger_type="manual",
            status="failed",
            failure_category="provider_error",
            outcome="technical_failure",
            tier=tier,
            provider_id=provider_id,
        )
        db.commit()
        return ConnectionTestResult(ok=False, message=str(exc))
    finally:
        budget.release(reservation)


@router.post("/models")
def add_model(payload: AiModelPreferenceIn, db: Session = Depends(get_db)) -> dict:
    catalog = ModelCatalogService(db)
    public = SettingsService(db).get_public()
    provider_id = OPENROUTER_PROVIDER_ID
    info = catalog.get_model(provider_id, payload.model_id)
    if info is not None:
        compatible, reason = check_compatibility(info, batch_size=public.batch_size)
        if not compatible:
            raise HTTPException(status_code=400, detail=reason)
        if _tier_value(info.pricing_tier) == "unknown" and not public.allow_unknown_pricing:
            raise HTTPException(
                status_code=400,
                detail="Unknown pricing is blocked unless you enable unknown-priced models.",
            )
    try:
        row = ModelPreferenceService(db).add(
            model_id=payload.model_id,
            tier=payload.tier,
            enabled=payload.enabled,
            provider_id=provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": row.id,
        "provider_id": row.provider_id,
        "model_id": row.model_id,
        "tier": row.tier,
        "priority": row.priority,
    }


@router.patch("/models/{pref_id}")
def patch_model(pref_id: int, payload: AiModelPatch, db: Session = Depends(get_db)) -> dict:
    try:
        row = ModelPreferenceService(db).update(pref_id, enabled=payload.enabled, tier=payload.tier)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": row.id,
        "provider_id": row.provider_id,
        "model_id": row.model_id,
        "tier": row.tier,
        "enabled": row.enabled,
        "priority": row.priority,
    }


@router.delete("/models/{pref_id}")
def delete_model(pref_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        ModelPreferenceService(db).delete(pref_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/models/reorder")
def reorder_models(payload: AiModelReorderIn, db: Session = Depends(get_db)) -> dict:
    try:
        rows = ModelPreferenceService(db).reorder(tier=payload.tier, ordered_ids=payload.ordered_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ids": [r.id for r in rows]}


@router.get("/routing", response_model=AiRoutingOut)
def get_routing(db: Session = Depends(get_db)) -> AiRoutingOut:
    SettingsService(db).get_or_create_row()
    return _routing_out(db)


@router.put("/routing", response_model=AiRoutingOut)
def put_routing(payload: AiRoutingUpdate, db: Session = Depends(get_db)) -> AiRoutingOut:
    # Compatibility wrapper: OpenRouter key fields delegate to ProviderAccountService.
    if payload.openrouter_api_key or payload.clear_openrouter_api_key:
        ProviderAccountService(db).set_credentials(
            OPENROUTER_PROVIDER_ID,
            api_key=payload.openrouter_api_key,
            clear_api_key=bool(payload.clear_openrouter_api_key),
        )
    SettingsService(db).update(
        SettingsUpdate(
            routing_strategy=payload.routing_strategy,
            allow_paid_fallback=payload.allow_paid_fallback,
            allow_free_fallback=payload.allow_free_fallback,
            allow_unknown_pricing=payload.allow_unknown_pricing,
            maximum_cost_per_job_usd=payload.maximum_cost_per_job_usd,
            clear_maximum_cost_per_job=payload.clear_maximum_cost_per_job,
            monthly_budget_enabled=payload.monthly_budget_enabled,
            monthly_budget_amount_usd=payload.monthly_budget_amount_usd,
            clear_monthly_budget_amount=payload.clear_monthly_budget_amount,
            allow_manual_budget_override=payload.allow_manual_budget_override,
            openrouter_log_full_exchanges=payload.openrouter_log_full_exchanges,
            openrouter_temperature=payload.openrouter_temperature,
        )
    )
    return _routing_out(db)


@router.get("/budget", response_model=AiBudgetOut)
def get_budget(db: Session = Depends(get_db)) -> AiBudgetOut:
    status = AiBudgetService(db).status()
    return AiBudgetOut(
        enabled=status.enabled,
        limit=status.limit_usd,
        used=status.used_usd,
        remaining=status.remaining_usd,
        reserved=micro_to_usd(status.reserved_micro_usd) or 0.0,
        percent_used=status.percent_used,
        allow_manual_override=status.allow_manual_override,
    )
