"""ai_usage_records remain the historical cost authority."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_app_config
from app.db import Base
from app.db.models import (
    AiUsageRecordRow,
    JobRow,
    OpenRouterCatalogCacheRow,
    SettingsRow,
)
from app.jobs.service import JobService
from app.services.ai_cost import micro_to_usd
from app.services.ai_usage import AiUsageService
from app.translation.openrouter.client import OpenRouterModelInfo


def _db(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'u.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="openai/gpt-4o-mini"))
    db.commit()
    return db


def _catalog(db, prompt: float, completion: float):
    info = OpenRouterModelInfo(
        id="openai/gpt-4o-mini",
        name="GPT-4o Mini",
        prompt_price_per_million=prompt,
        completion_price_per_million=completion,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
    )
    row = db.get(OpenRouterCatalogCacheRow, 1)
    if row is None:
        db.add(OpenRouterCatalogCacheRow(id=1, payload_json=[info.to_dict()], stale=False))
    else:
        row.payload_json = [info.to_dict()]
        row.stale = False
        db.add(row)
    db.commit()


def test_record_keeps_estimated_separate_from_actual(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _catalog(db, 0.15, 0.60)
    row = AiUsageService(db).record(
        model_id="openai/gpt-4o-mini",
        operation_type="translation",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        actual_cost_usd=0.50,
        tier="paid",
    )
    db.commit()
    assert row.actual_cost_micro_usd == 500_000
    assert row.estimated_cost_micro_usd == 750_000
    assert micro_to_usd(row.actual_cost_micro_usd) == pytest.approx(0.50)
    assert micro_to_usd(row.estimated_cost_micro_usd) == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_job_usage_uses_snapshot_not_current_catalog(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    job = JobRow(
        media_type="movie",
        media_path="/media/movie.mkv",
        media_title="Example",
        source_subtitle_path="/media/movie.en.srt",
        target_subtitle_path="/media/movie.pt-PT.srt",
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status="completed",
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
    )
    db.add(job)
    db.commit()
    db.add(
        AiUsageRecordRow(
            job_id=job.id,
            operation_type="translation",
            trigger_type="manual",
            model_id="openai/gpt-4o-mini",
            tier="paid",
            status="success",
            outcome="perfect_success",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            estimated_cost_micro_usd=450,
            actual_cost_micro_usd=400,
            input_price_micro_usd_per_million=150_000,
            output_price_micro_usd_per_million=600_000,
            pricing_timestamp=datetime.now(timezone.utc),
            pricing_source="openrouter",
        )
    )
    db.commit()
    _catalog(db, 99.0, 99.0)

    usage = await JobService(db).get_job_usage(job.id)
    assert usage is not None
    assert usage.pricing_source == "openrouter"
    assert usage.totals.cost_usd == pytest.approx(0.0004)
    assert usage.totals.requests == 1
    assert usage.exchanges[0].cost_estimated is False
