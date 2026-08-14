"""Display-only adaptive model ranking (never used for routing in v0.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiUsageRecordRow
from app.services.ai_cost import effective_cost_micro, micro_to_usd


@dataclass
class ModelRank:
    model_id: str
    request_count: int
    successful_request_count: int
    clean_success_rate: float | None
    repair_rate: float | None
    validation_failure_rate: float | None
    technical_failure_rate: float | None
    average_cost_per_clean_success_usd: float | None
    average_latency_ms: float | None
    p50_latency_ms: float | None
    last_used_at: datetime | None
    quality_score: float | None
    cost_score: float | None
    speed_score: float | None
    reliability_score: float | None
    adaptive_score: float | None
    adaptive_rank: int | None
    confidence: str  # insufficient | low | medium | high
    sample_count: int


def _confidence(n: int) -> str:
    if n < 10:
        return "insufficient"
    if n < 25:
        return "low"
    if n < 100:
        return "medium"
    return "high"


def _norm_invert(values: dict[str, float]) -> dict[str, float]:
    """Lower is better → 0..100, with equal values scoring 100."""
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi <= lo:
        return {k: 100.0 for k in values}
    return {k: 100.0 * (1.0 - (v - lo) / (hi - lo)) for k, v in values.items()}


class AiRankingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def rank_models(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ModelRank]:
        query = select(AiUsageRecordRow).where(AiUsageRecordRow.operation_type != "model_test")
        if start is not None:
            query = query.where(AiUsageRecordRow.created_at >= start)
        if end is not None:
            query = query.where(AiUsageRecordRow.created_at < end)
        rows = list(self.db.scalars(query).all())

        by_model: dict[str, list[AiUsageRecordRow]] = {}
        for row in rows:
            by_model.setdefault(row.model_id, []).append(row)

        raw: list[ModelRank] = []
        cost_for_score: dict[str, float] = {}
        speed_for_score: dict[str, float] = {}

        for model_id, items in by_model.items():
            n = len(items)
            successes = [r for r in items if r.status == "success"]
            perfect = [r for r in items if r.outcome == "perfect_success"]
            repaired = [r for r in items if r.outcome == "success_with_repair"]
            validation = [r for r in items if r.outcome == "validation_failure"]
            technical = [r for r in items if r.outcome == "technical_failure"]
            scored = [r for r in items if r.outcome not in {"budget_blocked", "cancelled", None}]
            denom = len(scored) or n

            clean_rate = (len(perfect) / denom) if denom else None
            repair_rate = (len(repaired) / denom) if denom else None
            val_rate = (len(validation) / denom) if denom else None
            tech_rate = (len(technical) / n) if n else None

            clean_costs = [effective_cost_micro(r) for r in perfect]
            avg_clean_cost = (sum(clean_costs) / len(clean_costs)) if clean_costs else None
            latencies = [int(r.latency_ms) for r in items if r.latency_ms is not None]
            avg_lat = (sum(latencies) / len(latencies)) if latencies else None
            p50 = float(median(latencies)) if len(latencies) >= 10 else None
            last_used = max((r.created_at for r in items if r.created_at), default=None)

            quality = None
            if clean_rate is not None:
                quality = max(0.0, min(100.0, clean_rate * 100.0 - (repair_rate or 0) * 25.0 * 100.0 / 100.0))
                quality = max(0.0, min(100.0, (clean_rate * 100.0) - ((repair_rate or 0) * 25.0)))

            reliability = None
            if tech_rate is not None:
                reliability = max(0.0, min(100.0, (1.0 - tech_rate) * 100.0))

            if avg_clean_cost is not None:
                cost_for_score[model_id] = float(avg_clean_cost)
            speed_val = p50 if p50 is not None else avg_lat
            if speed_val is not None:
                speed_for_score[model_id] = float(speed_val)

            raw.append(
                ModelRank(
                    model_id=model_id,
                    request_count=n,
                    successful_request_count=len(successes),
                    clean_success_rate=clean_rate,
                    repair_rate=repair_rate,
                    validation_failure_rate=val_rate,
                    technical_failure_rate=tech_rate,
                    average_cost_per_clean_success_usd=micro_to_usd(int(avg_clean_cost))
                    if avg_clean_cost is not None
                    else None,
                    average_latency_ms=avg_lat,
                    p50_latency_ms=p50,
                    last_used_at=last_used,
                    quality_score=quality,
                    cost_score=None,
                    speed_score=None,
                    reliability_score=reliability,
                    adaptive_score=None,
                    adaptive_rank=None,
                    confidence=_confidence(n),
                    sample_count=n,
                )
            )

        cost_scores = _norm_invert(cost_for_score)
        speed_scores = _norm_invert(speed_for_score)
        for item in raw:
            item.cost_score = cost_scores.get(item.model_id)
            item.speed_score = speed_scores.get(item.model_id)
            if item.confidence == "insufficient":
                continue
            q = item.quality_score if item.quality_score is not None else 50.0
            c = item.cost_score if item.cost_score is not None else 50.0
            s = item.speed_score if item.speed_score is not None else 50.0
            r = item.reliability_score if item.reliability_score is not None else 50.0
            item.adaptive_score = q * 0.50 + c * 0.25 + s * 0.20 + r * 0.05

        ranked = sorted(
            [x for x in raw if x.adaptive_score is not None],
            key=lambda x: (-(x.adaptive_score or 0), -x.sample_count, x.model_id),
        )
        for index, item in enumerate(ranked, start=1):
            item.adaptive_rank = index

        rest = [x for x in raw if x.adaptive_score is None]
        rest.sort(key=lambda x: (-x.sample_count, x.model_id))
        return ranked + rest
