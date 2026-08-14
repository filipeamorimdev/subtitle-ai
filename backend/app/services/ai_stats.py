"""Server-side AI usage aggregation for dashboards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AiRoutingEventRow, AiUsageRecordRow, JobRow
from app.services.ai_budget import AiBudgetService
from app.services.ai_cost import effective_cost_micro, micro_to_usd
from app.services.ai_ranking import AiRankingService


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
    return func.coalesce(AiUsageRecordRow.actual_cost_micro_usd, AiUsageRecordRow.estimated_cost_micro_usd, 0)


class AiStatsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _filtered(self, start: datetime | None, end: datetime | None):
        query = select(AiUsageRecordRow)
        if start is not None:
            query = query.where(AiUsageRecordRow.created_at >= start)
        if end is not None:
            query = query.where(AiUsageRecordRow.created_at < end)
        return query

    def _aggregate(self, start: datetime | None, end: datetime | None) -> dict[str, Any]:
        rows = list(self.db.scalars(self._filtered(start, end)).all())
        requests = len(rows)
        successful = sum(1 for r in rows if r.status == "success")
        failed = sum(1 for r in rows if r.status != "success")
        input_tokens = sum(r.input_tokens or 0 for r in rows)
        output_tokens = sum(r.output_tokens or 0 for r in rows)
        total_tokens = sum(r.total_tokens or 0 for r in rows)
        cost = sum(effective_cost_micro(r) for r in rows)
        latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
        free_requests = sum(1 for r in rows if r.tier == "free")
        paid_requests = sum(1 for r in rows if r.tier == "paid")
        free_tokens = sum((r.total_tokens or 0) for r in rows if r.tier == "free")
        paid_tokens = sum((r.total_tokens or 0) for r in rows if r.tier == "paid")
        paid_cost = sum(effective_cost_micro(r) for r in rows if r.tier == "paid")
        translations = [r for r in rows if r.operation_type in {"translation", "translation_retry", "translation_repair"}]
        avg_cost = (cost / len(translations)) if translations else None
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
            "average_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
            "free_requests": free_requests,
            "paid_requests": paid_requests,
            "free_tokens": free_tokens,
            "paid_tokens": paid_tokens,
            "paid_cost_usd": micro_to_usd(paid_cost) or 0.0,
        }

    def overview(self, period: str = "month") -> dict[str, Any]:
        start, end, prev_start, prev_end = period_bounds(period)
        current = self._aggregate(start, end)
        previous = self._aggregate(prev_start, prev_end) if prev_start else None
        budget = AiBudgetService(self.db).status()
        ranking = AiRankingService(self.db).rank_models(start=start, end=end)
        routing = self.recent_routing(limit=20)
        empty = current["requests"] == 0
        month = self._aggregate(*period_bounds("month")[:2])
        week = self._aggregate(*period_bounds("7d")[:2])
        today = self._aggregate(*period_bounds("today")[:2])
        return {
            "period": period,
            "empty": empty,
            "cost": {
                "current": current["cost_usd"],
                "previous": previous["cost_usd"] if previous else None,
            },
            "requests": current["requests"],
            "success_rate": current["success_rate"],
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
            "ranking": [
                {
                    "model_id": r.model_id,
                    "adaptive_rank": r.adaptive_rank,
                    "adaptive_score": r.adaptive_score,
                    "quality_score": r.quality_score,
                    "cost_score": r.cost_score,
                    "speed_score": r.speed_score,
                    "reliability_score": r.reliability_score,
                    "clean_success_rate": r.clean_success_rate,
                    "repair_rate": r.repair_rate,
                    "average_cost_per_clean_success_usd": r.average_cost_per_clean_success_usd,
                    "average_latency_ms": r.average_latency_ms,
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
        rows = list(self.db.scalars(self._filtered(current_start, current_end)).all())
        by_day: dict[str, int] = {}
        by_model: dict[str, dict[str, Any]] = {}
        free_cost = 0
        paid_cost = 0
        free_req = 0
        paid_req = 0
        for row in rows:
            day = (row.created_at or utcnow()).strftime("%Y-%m-%d")
            cost = effective_cost_micro(row)
            by_day[day] = by_day.get(day, 0) + cost
            bucket = by_model.setdefault(
                row.model_id,
                {"model_id": row.model_id, "requests": 0, "cost_micro_usd": 0, "tokens": 0},
            )
            bucket["requests"] += 1
            bucket["cost_micro_usd"] += cost
            bucket["tokens"] += row.total_tokens or 0
            if row.tier == "paid":
                paid_cost += cost
                paid_req += 1
            elif row.tier == "free":
                free_cost += cost
                free_req += 1
        series = [
            {"date": day, "cost_usd": micro_to_usd(micro) or 0.0}
            for day, micro in sorted(by_day.items())
        ]
        models = sorted(
            [
                {
                    **item,
                    "cost_usd": micro_to_usd(item["cost_micro_usd"]) or 0.0,
                }
                for item in by_model.values()
            ],
            key=lambda x: (-x["cost_usd"], -x["requests"]),
        )
        return {
            "period": period,
            "series": series,
            "by_model": models,
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
    ) -> dict[str, Any]:
        current_start, current_end, _, _ = period_bounds(period, start=start, end=end)
        query = self._filtered(current_start, current_end)
        if model:
            query = query.where(AiUsageRecordRow.model_id == model)
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
                    "model_id": row.model_id,
                    "tier": row.tier,
                    "trigger_type": row.trigger_type,
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "total_tokens": row.total_tokens,
                    "cost_usd": micro_to_usd(cost),
                    "status": row.status,
                    "failure_category": row.failure_category,
                    "outcome": row.outcome,
                    "latency_ms": row.latency_ms,
                }
            )

        breakdown_map: dict[str, dict[str, Any]] = {}
        all_rows = list(self.db.scalars(self._filtered(current_start, current_end)).all())
        for row in all_rows:
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
                "model_id": row.model_id,
                "next_model_id": row.next_model_id,
                "failure_category": row.failure_category,
                "detail": row.detail,
            }
            for row in rows
        ]
