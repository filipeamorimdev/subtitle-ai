"""Per-request exchange logs on the job detail API."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_app_config
from app.db import Base
from app.db.models import JobRow
from app.jobs.service import JobService
from app.translation.openrouter.exchange_log import job_openrouter_log_path


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _add_job(db: Session) -> JobRow:
    row = JobRow(
        job_kind="translate",
        media_type="movie",
        media_path="/media/movie.mkv",
        source_subtitle_path="/media/movie.en.srt",
        target_subtitle_path="/media/movie.pt.srt",
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status="completed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_list_and_get_job_request_logs(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    (config_dir / "logs" / "jobs").mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    db = _session()
    job = _add_job(db)
    path = job_openrouter_log_path(config_dir, job.id)
    entries = [
        {"event": "job_start", "model": "openai/gpt-4o-mini"},
        {
            "event": "exchange",
            "ts": "2026-08-17 11:32:37",
            "attempt": 1,
            "request": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional audiovisual subtitle translator.",
                    }
                ],
            },
            "response": {
                "status_code": 200,
                "body": {
                    "model": "openai/gpt-4o-mini",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                },
            },
            "error": None,
        },
        {
            "event": "exchange",
            "ts": "2026-08-17 11:33:47",
            "attempt": 1,
            "request": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract audiovisual glossary terms for subtitle translation consistency.",
                    }
                ],
            },
            "response": {
                "status_code": 200,
                "body": {
                    "model": "openai/gpt-4o-mini",
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 30,
                    },
                },
            },
            "error": None,
        },
        {"event": "job_end", "status": "completed"},
    ]
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")

    service = JobService(db)
    listed = service.list_job_requests(job.id)
    assert listed is not None
    assert len(listed) == 2
    assert listed[0].index == 1
    assert listed[0].action == "translate"
    assert listed[0].total_tokens == 150
    assert listed[0].ok is True
    assert listed[1].action == "glossary_extract"
    assert listed[1].total_tokens == 30

    first = service.get_job_request_log(job.id, 1)
    assert first is not None
    assert first.exists is True
    assert first.entry is not None
    assert first.entry["event"] == "exchange"
    assert first.entry["request"]["messages"][0]["content"].startswith("You are a professional")

    second = service.get_job_request_log(job.id, 2)
    assert second is not None
    assert second.entry is not None
    assert "glossary terms" in second.entry["request"]["messages"][0]["content"]

    assert service.get_job_request_log(job.id, 3) is None
    assert service.list_job_requests(99999) is None
    assert service.get_job_request_log(99999, 1) is None


def test_list_job_requests_empty_without_log(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    (config_dir / "logs" / "jobs").mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    db = _session()
    job = _add_job(db)
    listed = JobService(db).list_job_requests(job.id)
    assert listed == []
