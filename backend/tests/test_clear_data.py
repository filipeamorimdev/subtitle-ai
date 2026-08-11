"""Tests for settings clear/maintenance endpoints."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_app_config
from app.db import Base
from app.db.models import GlossaryScopeRow, GlossaryTermRow, JobRow, TranslationCacheRow
from app.jobs.service import JobService
from app.services.glossary import GlossaryService
from app.translation.openrouter.exchange_log import job_openrouter_log_path


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _add_job(
    db: Session,
    *,
    job_kind: str = "translate",
    status: str = "completed",
    tokens: int | None = 10,
) -> JobRow:
    row = JobRow(
        job_kind=job_kind,
        media_type="movie",
        media_path="/media/movie.mkv",
        source_subtitle_path="/media/movie.en.srt",
        target_subtitle_path="/media/movie.pt.srt",
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status=status,
        input_tokens=tokens,
        output_tokens=tokens,
        total_tokens=(tokens * 2) if tokens is not None else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_clear_jobs_by_kind_and_all(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    (config_dir / "logs" / "jobs").mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    db = _session()
    translate = _add_job(db, job_kind="translate")
    extract = _add_job(db, job_kind="extract")
    _add_job(db, job_kind="request")
    translate_id = translate.id
    extract_id = extract.id

    log_path = job_openrouter_log_path(config_dir, translate_id)
    log_path.write_text('{"event":"test"}\n', encoding="utf-8")
    db.add(
        TranslationCacheRow(
            source_hash="abc",
            target_language="pt-PT",
            model="openai/gpt-4o-mini",
            target_subtitle_path="/media/movie.pt.srt",
            job_id=translate_id,
        )
    )
    db.commit()

    service = JobService(db)
    result = service.clear_jobs(job_kind="translate")
    assert result.deleted == 1
    assert not log_path.exists()
    assert db.get(JobRow, translate_id) is None
    assert db.get(JobRow, extract_id) is not None
    assert db.scalar(select(TranslationCacheRow.id)) is None

    result_all = service.clear_jobs()
    assert result_all.deleted == 2
    assert db.scalar(select(JobRow.id)) is None

    get_app_config.cache_clear()


def test_clear_jobs_by_status(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    (config_dir / "logs" / "jobs").mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    db = _session()
    failed = _add_job(db, status="failed")
    skipped = _add_job(db, status="skipped")
    cancelled = _add_job(db, status="cancelled")
    completed = _add_job(db, status="completed")
    failed_id, skipped_id, cancelled_id, completed_id = (
        failed.id,
        skipped.id,
        cancelled.id,
        completed.id,
    )

    service = JobService(db)
    assert service.clear_jobs(status="failed").deleted == 1
    assert db.get(JobRow, failed_id) is None
    assert db.get(JobRow, skipped_id) is not None

    assert service.clear_jobs(status="skipped").deleted == 1
    assert db.get(JobRow, skipped_id) is None
    assert db.get(JobRow, cancelled_id) is not None

    assert service.clear_jobs(status="cancelled").deleted == 1
    assert db.get(JobRow, cancelled_id) is None
    assert db.get(JobRow, completed_id) is not None

    get_app_config.cache_clear()


def test_clear_usage_stats(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    logs_dir = config_dir / "logs" / "jobs"
    logs_dir.mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    db = _session()
    job = _add_job(db, tokens=42)
    log_path = job_openrouter_log_path(config_dir, job.id)
    log_path.write_text('{"event":"usage"}\n', encoding="utf-8")

    result = JobService(db).clear_usage_stats()
    assert result.deleted == 1
    assert not log_path.exists()
    refreshed = db.get(JobRow, job.id)
    assert refreshed is not None
    assert refreshed.input_tokens is None
    assert refreshed.output_tokens is None
    assert refreshed.total_tokens is None

    get_app_config.cache_clear()


def test_clear_glossaries_by_kind_and_all():
    db = _session()
    service = GlossaryService(db)
    universe = service.create_scope(
        kind="universe",
        key="marvel",
        display_name="Marvel",
        target_language="pt-PT",
    )
    series = service.create_scope(
        kind="series",
        key="wandavision",
        display_name="WandaVision",
        target_language="pt-PT",
        parent_scope_id=universe.id,
    )
    movie = service.create_scope(
        kind="movie",
        key="endgame",
        display_name="Endgame",
        target_language="pt-PT",
        parent_scope_id=universe.id,
    )
    service.create_term(universe.id, source="Tony", target="Tony", term_type="character")
    service.create_term(series.id, source="Wanda", target="Wanda", term_type="character")
    service.create_term(movie.id, source="Thanos", target="Thanos", term_type="character")

    deleted_movies = service.clear_scopes(kind="movie")
    assert deleted_movies == 1
    assert db.get(GlossaryScopeRow, movie.id) is None
    assert db.get(GlossaryScopeRow, universe.id) is not None
    assert db.scalar(
        select(GlossaryTermRow.id).where(GlossaryTermRow.scope_id == movie.id)
    ) is None

    deleted_all = service.clear_scopes()
    assert deleted_all == 2
    assert db.scalar(select(GlossaryScopeRow.id)) is None
    assert db.scalar(select(GlossaryTermRow.id)) is None
