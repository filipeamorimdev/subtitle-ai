"""Server-side AI usage aggregation for dashboards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.db.models import (
    AiRoutingEventRow,
    AiUsageRecordRow,
    JobRow,
    SettingsRow,
)
from app.services.ai_budget import AiBudgetService
from app.services.ai_cost import effective_cost_micro, micro_to_usd
from app.services.ai_ranking import TRANSLATION_RANKING_OPS, AiRankingService
from app.services.model_catalog import ModelCatalogService
from app.services.model_preferences import list_preferences
from app.translation.openrouter.client import batch_base_model


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_bounds(
    period: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[datetime | None, datetime | None, datetime | None, datetime | None]:
    """Return (current_start, current_end, previous_start, previous_end)."""
    now = utcnow()
    if period == "custom":
        return start, end, None, None
    if period == "all":
        return None, None, None, None
    if period == "today":
        current_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_start = current_start - timedelta(days=1)
        return current_start, now, prev_start, current_start
    if period == "week" or period == "7d":
        current_start = now - timedelta(days=7)
        return current_start, now, current_start - timedelta(days=7), current_start
    if period == "30d":
        current_start = now - timedelta(days=30)
        return current_start, now, current_start - timedelta(days=30), current_start
    # this_month / month
    current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if current_start.month == 1:
        prev_start = current_start.replace(year=current_start.year - 1, month=12)
    else:
        prev_start = current_start.replace(month=current_start.month - 1)
    return current_start, now, prev_start, current_start


def _cost_expr():
    return func.coalesce(
        AiUsageRecordRow.actual_cost_micro_usd,
        AiUsageRecordRow.estimated_cost_micro_usd,
        0,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _completed_job_duration_seconds(row: JobRow) -> float | None:
    start = _as_utc(row.started_at) or _as_utc(row.created_at)
    end = _as_utc(row.completed_at)
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds(), 3)


def _job_model_keys(provider_id: str, model_id: str) -> list[str]:
    keys = [f"{provider_id}|{model_id}", model_id]
    base = batch_base_model(model_id)
    if base != model_id:
        keys.append(f"{provider_id}|{base}")
        keys.append(base)
    return keys


class AiStatsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _apply_time(self, query, start: datetime | None, end: datetime | None):
        if start is not None:
            query = query.where(AiUsageRecordRow.created_at >= start)
        if end is not None:
            query = query.where(AiUsageRecordRow.created_at < end)
        return query

    def _filtered(self, start: datetime | None, end: datetime | None):
        return self._apply_time(select(AiUsageRecordRow), start, end)

    def _aggregate(self, start: datetime | None, end: datetime | None) -> dict[str, Any]:
        cost_col = _cost_expr()
        base = select(
            func.count(AiUsageRecordRow.id),
            func.coalesce(
                func.sum(case((AiUsageRecordRow.status == "success", 1), else_=0)), 0
            ),
            func.coalesce(func.sum(AiUsageRecordRow.input_tokens), 0),
            func.coalesce(func.sum(AiUsageRecordRow.output_tokens), 0),
            func.coalesce(func.sum(AiUsageRecordRow.total_tokens), 0),
            func.coalesce(func.sum(cost_col), 0),
            func.avg(AiUsageRecordRow.latency_ms),
            func.coalesce(
                func.sum(case((AiUsageRecordRow.tier == "free", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((AiUsageRecordRow.tier == "paid", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case(
                        (AiUsageRecordRow.tier == "free", AiUsageRecordRow.total_tokens),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (AiUsageRecordRow.tier == "paid", AiUsageRecordRow.total_tokens),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((AiUsageRecordRow.tier == "paid", cost_col), else_=0)), 0
            ),
        )
        base = self._apply_time(base, start, end)
        row = self.db.execute(base).one()
        requests = int(row[0] or 0)
        successful = int(row[1] or 0)
        failed = requests - successful
        input_tokens = int(row[2] or 0)
        output_tokens = int(row[3] or 0)
        total_tokens = int(row[4] or 0)
        cost = int(row[5] or 0)
        avg_latency = float(row[6]) if row[6] is not None else None
        free_requests = int(row[7] or 0)
        paid_requests = int(row[8] or 0)
        free_tokens = int(row[9] or 0)
        paid_tokens = int(row[10] or 0)
        paid_cost = int(row[11] or 0)

        # Correctness metrics over translation-related ops only.
        t_query = select(AiUsageRecordRow).where(
            AiUsageRecordRow.operation_type.in_(TRANSLATION_RANKING_OPS)
        )
        t_query = self._apply_time(t_query, start, end)
        t_rows = list(self.db.scalars(t_query).all())
        t_n = len(t_rows)
        scored = [r for r in t_rows if r.outcome not in {"budget_blocked", "cancelled", None}]
        denom = len(scored) or t_n or 1
        perfect = sum(1 for r in t_rows if r.outcome == "perfect_success")
        repaired = sum(1 for r in t_rows if r.outcome == "success_with_repair")
        validation = sum(1 for r in t_rows if r.outcome == "validation_failure")
        technical = sum(1 for r in t_rows if r.outcome == "technical_failure")
        avg_cost = (cost / t_n) if t_n else None

        return {
            "requests": requests,
            "successful_requests": successful,
            "failed_requests": failed,
            "success_rate": (successful / requests) if requests else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_micro_usd": cost,
            "cost_usd": micro_to_usd(cost) or 0.0,
            "average_cost_usd": micro_to_usd(int(avg_cost)) if avg_cost is not None else None,
            "average_latency_ms": avg_latency,
            "free_requests": free_requests,
            "paid_requests": paid_requests,
            "free_tokens": free_tokens,
            "paid_tokens": paid_tokens,
            "paid_cost_usd": micro_to_usd(paid_cost) or 0.0,
            "clean_success_rate": (perfect / denom) if t_n else None,
            "repair_rate": (repaired / denom) if t_n else None,
            "validation_failure_rate": (validation / denom) if t_n else None,
            "technical_failure_rate": (technical / t_n) if t_n else None,
            "translation_requests": t_n,
        }

    def _ai_status(self, budget, month_agg: dict[str, Any]) -> tuple[str, list[str]]:
        reasons: list[str] = []
        settings = self.db.get(SettingsRow, 1)
        openrouter_configured = bool(
            settings and getattr(settings, "openrouter_api_key_encrypted", None)
        )
        if not openrouter_configured:
            try:
                from app.ai.credentials import ProviderAccountService

                openrouter_configured = ProviderAccountService(self.db).is_configured("openrouter")
            except Exception:  # noqa: BLE001
                pass
        if not openrouter_configured:
            reasons.append("OpenRouter not configured")

        prefs = list_preferences(self.db, enabled_only=False)
        enabled = [p for p in prefs if p.enabled]
        if prefs and not enabled:
            reasons.append("All models disabled")

        catalog = ModelCatalogService(self.db)
        batch_size = int(getattr(settings, "batch_size", 25) or 25) if settings else 25
        unavailable = 0
        compatible_enabled = 0
        for pref in enabled:
            meta = catalog.annotate_model(pref.model_id, batch_size=batch_size)
            if meta.get("unavailable"):
                unavailable += 1
            elif meta.get("compatible", True):
                compatible_enabled += 1
        if unavailable:
            reasons.append(f"{unavailable} preferred model{'s' if unavailable != 1 else ''} unavailable")
        if enabled and compatible_enabled == 0 and not any("OpenRouter" in r for r in reasons):
            reasons.append("No compatible model")

        if budget.enabled and budget.percent_used is not None:
            if budget.percent_used >= 100 or (budget.remaining_micro_usd or 0) <= 0:
                reasons.append("Monthly budget exhausted")
            elif budget.percent_used >= 90:
                reasons.append(f"Monthly budget {budget.percent_used:.0f}% used")

        tech = month_agg.get("technical_failure_rate")
        if tech is not None and month_agg.get("translation_requests", 0) >= 10 and tech >= 0.25:
            reasons.append(f"High recent technical failure rate ({tech * 100:.0f}%)")

        if reasons:
            return "attention", reasons
        if month_agg.get("requests", 0) == 0:
            return "idle", []
        return "healthy", []

    def overview(self, period: str = "month") -> dict[str, Any]:
        start, end, prev_start, prev_end = period_bounds(period)
        current = self._aggregate(start, end)
        previous = self._aggregate(prev_start, prev_end) if prev_start else None
        budget = AiBudgetService(self.db).status()
        ranking = AiRankingService(self.db).rank_models(start=start, end=end)
        job_times = self.completed_job_duration_by_model(start, end)
        prefs = list_preferences(self.db, enabled_only=False)
        priority_by_model: dict[str, int] = {}
        for pref in prefs:
            priority_by_model[pref.model_id] = pref.priority
            priority_by_model[batch_base_model(pref.model_id)] = pref.priority
            pid = getattr(pref, "provider_id", None) or "openrouter"
            priority_by_model[f"{pid}|{pref.model_id}"] = pref.priority

        routing = self.recent_routing(limit=20)
        empty = current["requests"] == 0
        month = self._aggregate(*period_bounds("month")[:2])
        week = self._aggregate(*period_bounds("7d")[:2])
        today = self._aggregate(*period_bounds("today")[:2])
        status, status_reasons = self._ai_status(budget, month)
        active_jobs = (
            self.db.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.status.in_(["pending", "processing"]))
            )
            or 0
        )

        best = next((r for r in ranking if r.adaptive_rank == 1), None)

        return {
            "period": period,
            "empty": empty,
            "status": status,
            "status_reasons": status_reasons,
            "active_jobs": int(active_jobs),
            "cost": {
                "current": current["cost_usd"],
                "previous": previous["cost_usd"] if previous else None,
            },
            "requests": current["requests"],
            "success_rate": current["success_rate"],
            "clean_success_rate": current["clean_success_rate"],
            "repair_rate": current["repair_rate"],
            "validation_failure_rate": current["validation_failure_rate"],
            "technical_failure_rate": current["technical_failure_rate"],
            "tokens": {
                "input": current["input_tokens"],
                "output": current["output_tokens"],
                "total": current["total_tokens"],
            },
            "free_requests": current["free_requests"],
            "paid_requests": current["paid_requests"],
            "free_tokens": current["free_tokens"],
            "paid_tokens": current["paid_tokens"],
            "paid_cost_usd": current["paid_cost_usd"],
            "average_cost_usd": current["average_cost_usd"],
            "average_latency_ms": current["average_latency_ms"],
            "cards": {
                "month": month,
                "week": week,
                "today": today,
            },
            "budget": {
                "enabled": budget.enabled,
                "limit": budget.limit_usd,
                "used": budget.used_usd,
                "remaining": budget.remaining_usd,
                "reserved": micro_to_usd(budget.reserved_micro_usd) or 0.0,
                "percent_used": budget.percent_used,
                "allow_manual_override": budget.allow_manual_override,
            },
            "ai_summary": {
                "this_month_cost_usd": month["cost_usd"],
                "this_month_requests": month["requests"],
                "clean_success_rate": month["clean_success_rate"],
                "budget_percent_used": budget.percent_used,
                "best_model_id": best.model_id if best else None,
                "best_provider_id": best.provider_id if best else None,
                "status": status,
            },
            "ranking": [
                {
                    "provider_id": r.provider_id,
                    "provider_name": "OpenRouter" if r.provider_id == "openrouter" else r.provider_id.title(),
                    "model_id": r.model_id,
                    "configured_priority": priority_by_model.get(f"{r.provider_id}|{r.model_id}")
                    or priority_by_model.get(r.model_id)
                    or priority_by_model.get(batch_base_model(r.model_id)),
                    "adaptive_rank": r.adaptive_rank,
                    "adaptive_score": r.adaptive_score,
                    "quality_score": r.quality_score,
                    "cost_score": r.cost_score,
                    "speed_score": r.speed_score,
                    "reliability_score": r.reliability_score,
                    "clean_success_rate": r.clean_success_rate,
                    "repair_rate": r.repair_rate,
                    "validation_failure_rate": r.validation_failure_rate,
                    "technical_failure_rate": r.technical_failure_rate,
                    "average_cost_per_clean_success_usd": r.average_cost_per_clean_success_usd,
                    "average_latency_ms": r.average_latency_ms,
                    **(
                        job_times.get(f"{r.provider_id}|{r.model_id}")
                        or job_times.get(r.model_id)
                        or {
                            "average_job_duration_seconds": None,
                            "completed_job_count": 0,
                        }
                    ),
                    "sample_count": r.sample_count,
                    "confidence": r.confidence,
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                }
                for r in ranking
            ],
            "routing": routing,
        }

    def costs(
        self,
        *,
        period: str = "30d",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        current_start, current_end, _, _ = period_bounds(period, start=start, end=end)
        cost_col = _cost_expr()
        day_expr = func.strftime("%Y-%m-%d", AiUsageRecordRow.created_at)
        day_q = select(
            day_expr.label("day"),
            func.coalesce(func.sum(cost_col), 0),
            func.count(AiUsageRecordRow.id),
        ).group_by(day_expr)
        day_q = self._apply_time(day_q, current_start, current_end)
        day_rows = self.db.execute(day_q).all()
        series = [
            {
                "date": day,
                "cost_usd": micro_to_usd(int(micro or 0)) or 0.0,
                "request_count": int(count or 0),
            }
            for day, micro, count in sorted(day_rows, key=lambda r: r[0] or "")
        ]

        model_q = select(
            AiUsageRecordRow.model_id,
            func.count(AiUsageRecordRow.id),
            func.coalesce(func.sum(cost_col), 0),
            func.coalesce(func.sum(AiUsageRecordRow.total_tokens), 0),
        ).group_by(AiUsageRecordRow.model_id)
        model_q = self._apply_time(model_q, current_start, current_end)
        model_rows = self.db.execute(model_q).all()
        models = sorted(
            [
                {
                    "model_id": model_id,
                    "requests": int(req or 0),
                    "cost_micro_usd": int(micro or 0),
                    "cost_usd": micro_to_usd(int(micro or 0)) or 0.0,
                    "tokens": int(tokens or 0),
                }
                for model_id, req, micro, tokens in model_rows
            ],
            key=lambda x: (-x["cost_usd"], -x["requests"]),
        )

        tier_q = select(
            AiUsageRecordRow.tier,
            func.count(AiUsageRecordRow.id),
            func.coalesce(func.sum(cost_col), 0),
        ).group_by(AiUsageRecordRow.tier)
        tier_q = self._apply_time(tier_q, current_start, current_end)
        free_req = paid_req = free_cost = paid_cost = 0
        for tier, req, micro in self.db.execute(tier_q).all():
            if tier == "paid":
                paid_req = int(req or 0)
                paid_cost = int(micro or 0)
            elif tier == "free":
                free_req = int(req or 0)
                free_cost = int(micro or 0)

        fail_q = select(
            AiUsageRecordRow.failure_category,
            func.count(AiUsageRecordRow.id),
        ).where(
            AiUsageRecordRow.failure_category.is_not(None),
            AiUsageRecordRow.status != "success",
        ).group_by(AiUsageRecordRow.failure_category)
        fail_q = self._apply_time(fail_q, current_start, current_end)
        failure_categories = [
            {"category": cat or "unknown", "count": int(count or 0)}
            for cat, count in self.db.execute(fail_q).all()
        ]
        failure_categories.sort(key=lambda x: -x["count"])

        return {
            "period": period,
            "series": series,
            "by_model": models,
            "failure_categories": failure_categories,
            "free_vs_paid": {
                "free_requests": free_req,
                "paid_requests": paid_req,
                "free_cost_usd": micro_to_usd(free_cost) or 0.0,
                "paid_cost_usd": micro_to_usd(paid_cost) or 0.0,
            },
        }

    def usage_page(
        self,
        *,
        period: str = "30d",
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
        sort: str = "cost",
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        current_start, current_end, _, _ = period_bounds(period, start=start, end=end)
        query = self._filtered(current_start, current_end)
        if model:
            query = query.where(AiUsageRecordRow.model_id == model)
        if provider_id:
            query = query.where(AiUsageRecordRow.provider_id == provider_id)
        if tier:
            query = query.where(AiUsageRecordRow.tier == tier)
        if operation:
            query = query.where(AiUsageRecordRow.operation_type == operation)
        if trigger_type:
            query = query.where(AiUsageRecordRow.trigger_type == trigger_type)
        if status:
            query = query.where(AiUsageRecordRow.status == status)
        if failure:
            query = query.where(AiUsageRecordRow.failure_category == failure)

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            self.db.scalars(
                query.order_by(AiUsageRecordRow.created_at.desc(), AiUsageRecordRow.id.desc())
                .offset(max(0, offset))
                .limit(min(200, max(1, limit)))
            ).all()
        )
        job_ids = [r.job_id for r in rows if r.job_id]
        jobs: dict[int, JobRow] = {}
        if job_ids:
            for job in self.db.scalars(select(JobRow).where(JobRow.id.in_(job_ids))).all():
                jobs[job.id] = job

        items = []
        for row in rows:
            job = jobs.get(row.job_id) if row.job_id else None
            cost = effective_cost_micro(row)
            items.append(
                {
                    "id": row.id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "job_id": row.job_id,
                    "media_title": job.media_title if job else None,
                    "operation_type": row.operation_type,
                    "provider_id": getattr(row, "provider_id", None) or "openrouter",
                    "model_id": row.model_id,
                    "tier": row.tier,
                    "trigger_type": row.trigger_type,
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "total_tokens": row.total_tokens,
                    "cost_usd": micro_to_usd(cost),
                    "cost_source": getattr(row, "cost_source", None),
                    "request_id": getattr(row, "request_id", None),
                    "status": row.status,
                    "failure_category": row.failure_category,
                    "outcome": row.outcome,
                    "latency_ms": row.latency_ms,
                }
            )

        # by_model respects the same filters as the table (except pagination).
        filtered_all = list(self.db.scalars(query).all())
        breakdown_map: dict[str, dict[str, Any]] = {}
        for row in filtered_all:
            bucket = breakdown_map.setdefault(
                row.model_id,
                {
                    "model_id": row.model_id,
                    "requests": 0,
                    "successes": 0,
                    "failures": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_micro_usd": 0,
                    "latency_total": 0,
                    "latency_n": 0,
                    "perfect": 0,
                    "repaired": 0,
                    "validation": 0,
                    "technical": 0,
                },
            )
            bucket["requests"] += 1
            if row.status == "success":
                bucket["successes"] += 1
            else:
                bucket["failures"] += 1
            bucket["input_tokens"] += row.input_tokens or 0
            bucket["output_tokens"] += row.output_tokens or 0
            bucket["total_tokens"] += row.total_tokens or 0
            bucket["cost_micro_usd"] += effective_cost_micro(row)
            if row.latency_ms is not None:
                bucket["latency_total"] += row.latency_ms
                bucket["latency_n"] += 1
            if row.outcome == "perfect_success":
                bucket["perfect"] += 1
            elif row.outcome == "success_with_repair":
                bucket["repaired"] += 1
            elif row.outcome == "validation_failure":
                bucket["validation"] += 1
            elif row.outcome == "technical_failure":
                bucket["technical"] += 1

        breakdown = []
        for item in breakdown_map.values():
            req = item["requests"] or 1
            breakdown.append(
                {
                    "model_id": item["model_id"],
                    "requests": item["requests"],
                    "successes": item["successes"],
                    "failures": item["failures"],
                    "success_rate": item["successes"] / req,
                    "input_tokens": item["input_tokens"],
                    "output_tokens": item["output_tokens"],
                    "total_tokens": item["total_tokens"],
                    "cost_usd": micro_to_usd(item["cost_micro_usd"]) or 0.0,
                    "average_latency_ms": (item["latency_total"] / item["latency_n"])
                    if item["latency_n"]
                    else None,
                    "clean_success_rate": item["perfect"] / req,
                    "repair_rate": item["repaired"] / req,
                    "validation_failure_rate": item["validation"] / req,
                    "technical_failure_rate": item["technical"] / req,
                }
            )
        reverse = True
        key = sort if sort in {"requests", "success_rate", "total_tokens", "cost_usd"} else "cost_usd"
        breakdown.sort(key=lambda x: (x.get(key) or 0), reverse=reverse)

        return {
            "total": int(total),
            "offset": offset,
            "limit": limit,
            "items": items,
            "by_model": breakdown,
            "totals": self._aggregate(current_start, current_end),
        }

    def _completed_translate_jobs(
        self,
        start: datetime | None,
        end: datetime | None,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> list[tuple[JobRow, float]]:
        query = select(JobRow).where(
            JobRow.status == "completed",
            JobRow.job_kind == "translate",
            JobRow.completed_at.is_not(None),
        )
        if start is not None:
            query = query.where(JobRow.completed_at >= start)
        if end is not None:
            query = query.where(JobRow.completed_at < end)
        if model_id:
            base = batch_base_model(model_id)
            if base != model_id:
                query = query.where(JobRow.model.in_([model_id, base]))
            else:
                query = query.where(JobRow.model == model_id)
        if provider_id:
            query = query.where(
                func.coalesce(JobRow.provider_id, OPENROUTER_PROVIDER_ID) == provider_id
            )
        items: list[tuple[JobRow, float]] = []
        for job in self.db.scalars(query).all():
            if not (job.model or "").strip():
                continue
            duration = _completed_job_duration_seconds(job)
            if duration is None:
                continue
            items.append((job, duration))
        return items

    def completed_job_duration_by_model(
        self,
        start: datetime | None,
        end: datetime | None,
    ) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[float]] = {}
        providers: dict[str, tuple[str, str]] = {}
        for job, duration in self._completed_translate_jobs(start, end):
            provider_id = getattr(job, "provider_id", None) or OPENROUTER_PROVIDER_ID
            model_id = job.model
            primary = f"{provider_id}|{model_id}"
            buckets.setdefault(primary, []).append(duration)
            providers[primary] = (provider_id, model_id)

        stats: dict[str, dict[str, Any]] = {}
        for primary, durations in buckets.items():
            payload = {
                "average_job_duration_seconds": sum(durations) / len(durations),
                "completed_job_count": len(durations),
            }
            provider_id, model_id = providers[primary]
            for key in _job_model_keys(provider_id, model_id):
                existing = stats.get(key)
                if existing is None or existing["completed_job_count"] < payload["completed_job_count"]:
                    stats[key] = payload
        return stats

    def completed_jobs_for_model(
        self,
        *,
        period: str = "month",
        provider_id: str | None = None,
        model_id: str,
        limit: int = 250,
    ) -> dict[str, Any]:
        start, end, _, _ = period_bounds(period)
        provider = provider_id or OPENROUTER_PROVIDER_ID
        rows = self._completed_translate_jobs(
            start, end, provider_id=provider, model_id=model_id
        )
        rows.sort(
            key=lambda pair: (
                _as_utc(pair[0].completed_at) or datetime.min.replace(tzinfo=timezone.utc),
                pair[0].id,
            ),
            reverse=True,
        )
        durations = [duration for _, duration in rows]
        average = (sum(durations) / len(durations)) if durations else None
        cap = min(500, max(1, limit))
        items = []
        for job, duration in rows[:cap]:
            items.append(
                {
                    "job_id": job.id,
                    "media_title": job.media_title,
                    "status": job.status,
                    "duration_seconds": duration,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "provider_id": getattr(job, "provider_id", None) or OPENROUTER_PROVIDER_ID,
                    "model_id": job.model,
                    "trigger_type": job.trigger_type,
                }
            )
        return {
            "period": period,
            "provider_id": provider,
            "model_id": model_id,
            "average_job_duration_seconds": average,
            "completed_job_count": len(rows),
            "items": items,
        }

    def recent_routing(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = list(
            self.db.scalars(
                select(AiRoutingEventRow)
                .order_by(AiRoutingEventRow.created_at.desc(), AiRoutingEventRow.id.desc())
                .limit(limit)
            ).all()
        )
        return [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "job_id": row.job_id,
                "event": row.event,
                "strategy": row.strategy,
                "provider_id": getattr(row, "provider_id", None) or "openrouter",
                "model_id": row.model_id,
                "next_provider_id": getattr(row, "next_provider_id", None),
                "next_model_id": row.next_model_id,
                "failure_category": row.failure_category,
                "detail": row.detail,
            }
            for row in rows
        ]
