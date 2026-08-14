"""AI statistics aggregation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import AiUsageRecordRow, SettingsRow
from app.services.ai_stats import AiStatsService


def test_stats_aggregates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="x", monthly_budget_enabled=False))
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            AiUsageRecordRow(
                operation_type="translation",
                trigger_type="automatic",
                model_id="free/a",
                tier="free",
                status="success",
                outcome="perfect_success",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                estimated_cost_micro_usd=0,
                actual_cost_micro_usd=0,
                latency_ms=1800,
                created_at=now,
            ),
            AiUsageRecordRow(
                operation_type="translation",
                trigger_type="manual",
                model_id="paid/c",
                tier="paid",
                status="success",
                outcome="success_with_repair",
                input_tokens=200,
                output_tokens=80,
                total_tokens=280,
                estimated_cost_micro_usd=4100,
                actual_cost_micro_usd=4100,
                latency_ms=3100,
                created_at=now,
            ),
            AiUsageRecordRow(
                operation_type="translation",
                trigger_type="automatic",
                model_id="free/a",
                tier="free",
                status="failed",
                failure_category="rate_limit",
                outcome="technical_failure",
                input_tokens=10,
                output_tokens=0,
                total_tokens=10,
                estimated_cost_micro_usd=0,
                latency_ms=500,
                created_at=now,
            ),
        ]
    )
    db.commit()
    overview = AiStatsService(db).overview("all")
    assert overview["requests"] == 3
    assert overview["free_requests"] == 2
    assert overview["paid_requests"] == 1
    assert overview["tokens"]["total"] == 440
    assert overview["paid_cost_usd"] == pytest.approx(0.0041)
    usage = AiStatsService(db).usage_page(period="all")
    assert usage["total"] == 3
    models = {m["model_id"]: m for m in usage["by_model"]}
    assert models["free/a"]["requests"] == 2
    assert models["paid/c"]["cost_usd"] > 0
    failed = [r for r in usage["items"] if r["status"] == "failed"]
    assert failed[0]["failure_category"] == "rate_limit"
    assert failed[0]["trigger_type"] == "automatic"
