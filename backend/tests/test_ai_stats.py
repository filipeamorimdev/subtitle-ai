"""AI statistics aggregation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import AiUsageRecordRow, JobRow, SettingsRow
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
                operation_type="translation_repair",
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
    assert overview["cards"]["today"]["translation_cost_usd"] == pytest.approx(0)
    assert overview["cards"]["today"]["repair_cost_usd"] == pytest.approx(0.0041)
    usage = AiStatsService(db).usage_page(period="all")
    assert usage["total"] == 3
    models = {m["model_id"]: m for m in usage["by_model"]}
    assert models["free/a"]["requests"] == 2
    assert models["paid/c"]["cost_usd"] > 0
    failed = [r for r in usage["items"] if r["status"] == "failed"]
    assert failed[0]["failure_category"] == "rate_limit"
    assert failed[0]["trigger_type"] == "automatic"


def test_overview_separates_translation_and_repair_costs(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'costs.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="x", monthly_budget_enabled=False))
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            AiUsageRecordRow(
                operation_type="translation",
                trigger_type="manual",
                model_id="paid/a",
                tier="paid",
                status="success",
                estimated_cost_micro_usd=1200,
                actual_cost_micro_usd=1200,
                created_at=now,
            ),
            AiUsageRecordRow(
                operation_type="translation_repair",
                trigger_type="manual",
                model_id="paid/a",
                tier="paid",
                status="success",
                estimated_cost_micro_usd=800,
                actual_cost_micro_usd=800,
                created_at=now,
            ),
        ]
    )
    db.commit()

    overview = AiStatsService(db).overview("today")
    assert overview["cards"]["today"]["translation_cost_usd"] == pytest.approx(0.0012)
    assert overview["cards"]["today"]["repair_cost_usd"] == pytest.approx(0.0008)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="x", monthly_budget_enabled=False))
    db.commit()
    return db


def _usage(db, *, model_id: str, now: datetime | None = None):
    db.add(
        AiUsageRecordRow(
            operation_type="translation",
            trigger_type="manual",
            provider_id="openrouter",
            model_id=model_id,
            tier="paid",
            status="success",
            outcome="perfect_success",
            latency_ms=1000,
            created_at=now or datetime.now(timezone.utc),
        )
    )
    db.commit()


def _job(
    db,
    *,
    model: str,
    status: str = "completed",
    duration_s: int = 10,
    completed_at: datetime | None = None,
    title: str = "Movie",
    job_kind: str = "translate",
    provider_id: str | None = "openrouter",
):
    end = completed_at or datetime.now(timezone.utc)
    start = end - timedelta(seconds=duration_s)
    job = JobRow(
        media_type="movie",
        media_path="/media/movie.mkv",
        media_title=title,
        source_subtitle_path="/media/movie.en.srt",
        target_subtitle_path="/media/movie.pt-PT.srt",
        source_language="en",
        target_language="pt-PT",
        provider_id=provider_id,
        model=model,
        job_kind=job_kind,
        status=status,
        trigger_type="manual",
        created_at=start,
        started_at=start,
        completed_at=end if status != "processing" else None,
    )
    db.add(job)
    db.commit()
    return job


def test_overview_mean_job_time_uses_successful_finished_jobs(tmp_path):
    db = _session(tmp_path)
    now = datetime.now(timezone.utc)
    _usage(db, model_id="paid/a", now=now)
    _job(db, model="paid/a", duration_s=10, title="Fast", completed_at=now)
    _job(db, model="paid/a", duration_s=20, title="Slow", completed_at=now)
    _job(db, model="paid/a", status="failed", duration_s=100, title="Failed", completed_at=now)
    _job(db, model="paid/a", status="processing", duration_s=50, title="Running", completed_at=now)
    _job(db, model="paid/a", job_kind="extract", duration_s=8, title="Extract", completed_at=now)
    _job(db, model="paid/other", duration_s=4, title="Other", completed_at=now)

    overview = AiStatsService(db).overview("all")
    row = next(r for r in overview["ranking"] if r["model_id"] == "paid/a")
    assert row["completed_job_count"] == 2
    assert row["average_job_duration_seconds"] == pytest.approx(15)

    listing = AiStatsService(db).completed_jobs_for_model(
        period="all", provider_id="openrouter", model_id="paid/a"
    )
    assert listing["completed_job_count"] == 2
    assert listing["average_job_duration_seconds"] == pytest.approx(15)
    assert {item["media_title"] for item in listing["items"]} == {"Fast", "Slow"}
    assert all(item["status"] == "completed" for item in listing["items"])


def test_mean_job_time_respects_period(tmp_path):
    db = _session(tmp_path)
    now = datetime.now(timezone.utc)
    _usage(db, model_id="paid/a", now=now)
    _job(db, model="paid/a", duration_s=10, title="Recent", completed_at=now)
    _job(
        db,
        model="paid/a",
        duration_s=40,
        title="Old",
        completed_at=now - timedelta(days=40),
    )
    recent = AiStatsService(db).overview("7d")
    row = next(r for r in recent["ranking"] if r["model_id"] == "paid/a")
    assert row["completed_job_count"] == 1
    assert row["average_job_duration_seconds"] == pytest.approx(10)

    all_time = AiStatsService(db).completed_jobs_for_model(period="all", model_id="paid/a")
    assert all_time["completed_job_count"] == 2
    assert all_time["average_job_duration_seconds"] == pytest.approx(25)
