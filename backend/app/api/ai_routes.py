"""AI model routing, catalog, usage, and budget APIs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

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
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError, batch_base_model

router = APIRouter(prefix="/api/ai", tags=["ai"])


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
    )


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
    )


@router.get("/costs")
def ai_costs(
    period: str = Query(default="30d"),
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return AiStatsService(db).costs(period=period, start=start, end=end)


@router.get("/models")
async def ai_models(db: Session = Depends(get_db)) -> dict:
    settings = SettingsService(db)
    public = settings.get_public()
    catalog = ModelCatalogService(db)
    snapshot = catalog.get_cached()
    if snapshot is None:
        try:
            snapshot = await catalog.get_models()
        except OpenRouterError:
            snapshot = catalog.get_cached()
    prefs = ModelPreferenceService(db).list_all()
    start, end, _, _ = period_bounds("30d")
    ranking = {r.model_id: r for r in AiRankingService(db).rank_models(start=start, end=end)}
    catalog_models = []
    if snapshot:
        for model in snapshot.models:
            compatible, reason = check_compatibility(model, batch_size=public.batch_size)
            catalog_models.append(
                {
                    "id": model.id,
                    "name": model.name,
                    "description": model.description,
                    "prompt_price_per_million": model.prompt_price_per_million,
                    "completion_price_per_million": model.completion_price_per_million,
                    "context_length": model.context_length,
                    "pricing_tier": model.pricing_tier,
                    "compatible": compatible,
                    "compatibility_reason": reason,
                    "stale": snapshot.stale,
                    "unavailable": False,
                    "input_modalities": model.input_modalities,
                    "output_modalities": model.output_modalities,
                }
            )
    preferred = []
    for pref in prefs:
        meta = catalog.annotate_model(pref.model_id, batch_size=public.batch_size)
        rank = ranking.get(pref.model_id) or ranking.get(batch_base_model(pref.model_id))
        preferred.append(
            {
                "id": pref.id,
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
    key, _ = settings.get_openrouter_credentials()
    return {
        "openrouter_configured": bool(key),
        "openrouter_api_key_masked": public.openrouter_api_key_masked,
        "catalog_fetched_at": snapshot.fetched_at.isoformat() if snapshot and snapshot.fetched_at else None,
        "catalog_stale": bool(snapshot.stale) if snapshot else True,
        "catalog_age_seconds": snapshot.age_seconds if snapshot else None,
        "preferences": preferred,
        "catalog": catalog_models,
        "routing": _routing_out(db).model_dump(),
    }


@router.post("/models/refresh")
async def refresh_models(db: Session = Depends(get_db)) -> dict:
    catalog = ModelCatalogService(db)
    try:
        snapshot = await catalog.get_models(force_refresh=True)
    except OpenRouterError as exc:
        cached = catalog.get_cached()
        if cached is None:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": False,
            "stale": True,
            "message": str(exc),
            "fetched_at": cached.fetched_at.isoformat() if cached.fetched_at else None,
            "count": len(cached.models),
        }
    return {
        "ok": True,
        "stale": snapshot.stale,
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        "count": len(snapshot.models),
    }


@router.post("/models/test", response_model=ConnectionTestResult)
async def test_model(payload: AiModelTestIn, db: Session = Depends(get_db)) -> ConnectionTestResult:
    settings = SettingsService(db)
    key, _ = settings.get_openrouter_credentials()
    if not key:
        return ConnectionTestResult(ok=False, message="OpenRouter API key is not configured.")
    public = settings.get_public()
    catalog = ModelCatalogService(db)
    info = catalog.get_model(payload.model_id)
    tier = info.pricing_tier if info else "unknown"
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
        client = OpenRouterClient(key)
        result = await client.chat_completion(
            model=batch_base_model(payload.model_id),
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
        )
        db.commit()
        return ConnectionTestResult(
            ok=True,
            message=f"Model {payload.model_id} responded.",
            details={"ok": True, "model": result.model, "sample": (result.content or "")[:80]},
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
        )
        db.commit()
        return ConnectionTestResult(ok=False, message=str(exc))
    except OpenRouterError as exc:
        usage.record(
            model_id=payload.model_id,
            operation_type="model_test",
            trigger_type="manual",
            status="failed",
            failure_category="provider_error",
            outcome="technical_failure",
            tier=tier,
        )
        db.commit()
        return ConnectionTestResult(ok=False, message=str(exc))
    finally:
        budget.release(reservation)


@router.post("/models")
def add_model(payload: AiModelPreferenceIn, db: Session = Depends(get_db)) -> dict:
    catalog = ModelCatalogService(db)
    public = SettingsService(db).get_public()
    info = catalog.get_model(payload.model_id)
    if info is not None:
        compatible, reason = check_compatibility(info, batch_size=public.batch_size)
        if not compatible:
            raise HTTPException(status_code=400, detail=reason)
        if info.pricing_tier == "unknown" and not public.allow_unknown_pricing:
            raise HTTPException(
                status_code=400,
                detail="Unknown pricing is blocked unless you enable unknown-priced models.",
            )
    try:
        row = ModelPreferenceService(db).add(
            model_id=payload.model_id, tier=payload.tier, enabled=payload.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "model_id": row.model_id, "tier": row.tier, "priority": row.priority}


@router.patch("/models/{pref_id}")
def patch_model(pref_id: int, payload: AiModelPatch, db: Session = Depends(get_db)) -> dict:
    try:
        row = ModelPreferenceService(db).update(pref_id, enabled=payload.enabled, tier=payload.tier)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "model_id": row.model_id, "tier": row.tier, "enabled": row.enabled, "priority": row.priority}


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
            openrouter_api_key=payload.openrouter_api_key,
            clear_openrouter_api_key=payload.clear_openrouter_api_key,
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
