"""Adaptive ranking and outcome attribution tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import AiUsageRecordRow, OpenRouterModelPreferenceRow, SettingsRow
from app.services.ai_ranking import AiRankingService
from app.services.ai_usage import AiUsageService
from app.services.model_router import ModelRouter, RoutingPolicy


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rank.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="x", monthly_budget_enabled=False))
    db.commit()
    return db


def _add(
    db,
    *,
    model_id: str,
    operation_type: str = "translation",
    outcome: str = "perfect_success",
    status: str = "success",
    cost: int = 1000,
    latency_ms: int = 1000,
    created_at: datetime | None = None,
    n: int = 1,
    job_id: int | None = None,
):
    now = created_at or datetime.now(timezone.utc)
    for i in range(n):
        db.add(
            AiUsageRecordRow(
                job_id=job_id,
                operation_type=operation_type,
                trigger_type="automatic",
                model_id=model_id,
                tier="paid",
                status=status,
                outcome=outcome,
                estimated_cost_micro_usd=cost,
                actual_cost_micro_usd=cost,
                latency_ms=latency_ms,
                created_at=now - timedelta(seconds=i),
            )
        )
    db.commit()


def test_clean_ranks_above_repaired(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="clean/a", outcome="perfect_success", n=20)
    _add(db, model_id="repair/b", outcome="success_with_repair", n=20)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert ranks["clean/a"].adaptive_rank == 1
    assert ranks["repair/b"].adaptive_rank == 2
    assert (ranks["clean/a"].adaptive_score or 0) > (ranks["repair/b"].adaptive_score or 0)


def test_clean_ranks_above_validation_failure(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="clean/a", outcome="perfect_success", n=20)
    _add(db, model_id="fail/b", outcome="validation_failure", status="failed", n=20)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert ranks["clean/a"].adaptive_rank == 1
    assert (ranks["clean/a"].adaptive_score or 0) > (ranks["fail/b"].adaptive_score or 0)


def test_repair_worse_than_validation_penalty_direction(tmp_path):
    """Validation failures penalize quality more than repairs at the same clean rate."""
    db = _db(tmp_path)
    # 50% clean / 50% repair
    _add(db, model_id="repaired/a", outcome="perfect_success", n=10)
    _add(db, model_id="repaired/a", outcome="success_with_repair", n=10)
    # 50% clean / 50% validation
    _add(db, model_id="invalid/b", outcome="perfect_success", n=10)
    _add(db, model_id="invalid/b", outcome="validation_failure", status="failed", n=10)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert (ranks["repaired/a"].quality_score or 0) > (ranks["invalid/b"].quality_score or 0)


def test_lower_cost_improves_ranking(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="cheap/a", outcome="perfect_success", cost=100, n=20)
    _add(db, model_id="pricey/b", outcome="perfect_success", cost=10_000, n=20)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert ranks["cheap/a"].adaptive_rank == 1


def test_lower_latency_improves_ranking(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="fast/a", outcome="perfect_success", latency_ms=500, cost=1000, n=20)
    _add(db, model_id="slow/b", outcome="perfect_success", latency_ms=5000, cost=1000, n=20)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert ranks["fast/a"].adaptive_rank == 1


def test_technical_failures_reduce_reliability(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="solid/a", outcome="perfect_success", n=20)
    _add(db, model_id="flaky/b", outcome="perfect_success", n=10)
    _add(
        db,
        model_id="flaky/b",
        outcome="technical_failure",
        status="failed",
        n=10,
    )
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert (ranks["solid/a"].reliability_score or 0) > (ranks["flaky/b"].reliability_score or 0)


def test_confidence_buckets(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="tiny/a", n=5)
    _add(db, model_id="low/b", n=15)
    _add(db, model_id="med/c", n=40)
    _add(db, model_id="high/d", n=120)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert ranks["tiny/a"].confidence == "insufficient"
    assert ranks["tiny/a"].adaptive_rank is None
    assert ranks["low/b"].confidence == "low"
    assert ranks["med/c"].confidence == "medium"
    assert ranks["high/d"].confidence == "high"


def test_model_test_and_glossary_excluded(tmp_path):
    db = _db(tmp_path)
    _add(db, model_id="prod/a", operation_type="translation", n=20)
    _add(db, model_id="retry/a", operation_type="translation_retry", n=20)
    _add(db, model_id="repair/a", operation_type="translation_repair", n=20)
    _add(db, model_id="test/b", operation_type="model_test", n=50)
    _add(db, model_id="gloss/c", operation_type="glossary_extract", n=50)
    _add(db, model_id="univ/d", operation_type="glossary_universe", n=50)
    ranks = {r.model_id: r for r in AiRankingService(db).rank_models()}
    assert "prod/a" in ranks
    assert "retry/a" in ranks
    assert "repair/a" in ranks
    assert "test/b" not in ranks
    assert "gloss/c" not in ranks
    assert "univ/d" not in ranks


def test_translation_outcomes_not_painted_on_glossary(tmp_path):
    from sqlalchemy import select

    db = _db(tmp_path)
    db.add(
        AiUsageRecordRow(
            job_id=7,
            operation_type="glossary_extract",
            trigger_type="manual",
            model_id="m/a",
            tier="paid",
            status="success",
            outcome=None,
        )
    )
    db.add(
        AiUsageRecordRow(
            job_id=7,
            operation_type="translation",
            trigger_type="manual",
            model_id="m/a",
            tier="paid",
            status="success",
            outcome=None,
        )
    )
    db.commit()
    AiUsageService(db).set_translation_outcomes(7, "perfect_success")
    db.commit()
    rows = list(db.scalars(select(AiUsageRecordRow).where(AiUsageRecordRow.job_id == 7)).all())
    by_op = {r.operation_type: r.outcome for r in rows}
    assert by_op["translation"] == "perfect_success"
    assert by_op["glossary_extract"] is None


def test_period_filtering(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    _add(db, model_id="old/a", n=20, created_at=now - timedelta(days=40))
    _add(db, model_id="new/b", n=20, created_at=now - timedelta(days=2))
    week = AiRankingService(db).rank_models(start=now - timedelta(days=7), end=now)
    ids = {r.model_id for r in week}
    assert "new/b" in ids
    assert "old/a" not in ids
    all_time = AiRankingService(db).rank_models()
    assert {r.model_id for r in all_time} == {"old/a", "new/b"}


def test_router_unaffected_by_ranking(tmp_path):
    from app.db.models import OpenRouterCatalogCacheRow
    from app.translation.openrouter.client import OpenRouterModelInfo

    db = _db(tmp_path)
    # Adaptive would prefer model B (cheaper), but user priority puts A first.
    _add(db, model_id="paid/b", cost=100, n=30)
    _add(db, model_id="paid/a", cost=10_000, n=30)
    db.add(OpenRouterModelPreferenceRow(model_id="paid/a", tier="paid", priority=1, enabled=True))
    db.add(OpenRouterModelPreferenceRow(model_id="paid/b", tier="paid", priority=2, enabled=True))
    payload = []
    for model_id, prompt, completion in [
        ("paid/a", 1.0, 2.0),
        ("paid/b", 0.1, 0.2),
    ]:
        info = OpenRouterModelInfo(
            id=model_id,
            name=model_id,
            prompt_price_per_million=prompt,
            completion_price_per_million=completion,
            context_length=128000,
            input_modalities=["text"],
            output_modalities=["text"],
        )
        payload.append(info.to_dict())
    db.add(OpenRouterCatalogCacheRow(id=1, payload_json=payload, stale=False))
    db.commit()
    ranks = AiRankingService(db).rank_models()
    assert ranks[0].model_id == "paid/b"
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="paid_only"))
    assert [c.model_id for c in result.candidates] == ["paid/a", "paid/b"]
