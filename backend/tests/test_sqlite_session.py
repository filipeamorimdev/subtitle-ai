"""SQLite session release around long I/O."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_app_config
from app.db import Base, release_session_connection
from app.db.models import JobRow
from app.jobs.service import JobService


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path))
    get_app_config.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return factory, tmp_path


def test_release_session_connection_ends_transaction(session_env):
    factory, _tmp_path = session_env
    db = factory()
    db.execute(select(JobRow).limit(1)).all()
    assert db.in_transaction()
    release_session_connection(db)
    assert not db.in_transaction()
    db.close()


@pytest.mark.asyncio
async def test_extract_job_releases_session_during_ffmpeg(session_env, monkeypatch):
    factory, tmp_path = session_env
    media = tmp_path / "Movie.mkv"
    media.write_text("x")
    output = tmp_path / "Movie.en.srt"
    db = factory()
    row = JobRow(
        job_kind="extract",
        status="processing",
        media_type="movie",
        media_path=str(media),
        source_subtitle_path=str(output),
        target_subtitle_path=str(output),
        source_language="en",
        target_language="pt-PT",
        extract_stream_index=2,
        bazarr_movie_id=1,
        model="ffmpeg",
    )
    db.add(row)
    db.commit()
    job_id = row.id

    seen: dict[str, object] = {}

    async def fake_extract(media_path, stream_index, output_path, language="en"):  # noqa: ARG001
        seen["extract_in_transaction"] = db.in_transaction()
        other = factory()
        try:
            other_row = other.get(JobRow, job_id)
            assert other_row is not None
            other_row.progress_detail = "concurrent"
            other.add(other_row)
            other.commit()
            seen["concurrent_write"] = True
        finally:
            other.close()
        Path(output_path).write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8"
        )

    async def fake_rescan(self, job):  # noqa: ARG001, ANN001
        seen["rescan_in_transaction"] = self.db.in_transaction()

    monkeypatch.setattr("app.jobs.service.extract_embedded_track", fake_extract)
    monkeypatch.setattr(JobService, "_rescan", fake_rescan)

    await JobService(db)._process_extract_job(job_id)
    db.expire_all()
    done = db.get(JobRow, job_id)
    assert seen.get("extract_in_transaction") is False
    assert seen.get("concurrent_write") is True
    assert done is not None
    assert done.status == "completed"
    db.close()
