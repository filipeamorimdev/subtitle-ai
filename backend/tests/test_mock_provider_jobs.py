"""Provider-neutral job creation and MockAIProvider execution."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai.bootstrap import bootstrap_providers
from app.ai.models import AIResponse
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.registry import get_provider_registry, reset_provider_registry
from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import (
    AiModelPreferenceRow,
    AiRoutingEventRow,
    AiUsageRecordRow,
    JobRow,
    TranslationCacheRow,
)
from app.jobs.service import JobService
from app.services.settings import SettingsService


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello

2
00:00:04,000 --> 00:00:06,000
World
"""


def _mock_response_factory(*, model_id, messages, request_id, **kwargs):
    system = next((m.get("content") for m in messages if m.get("role") == "system"), "")
    if "Classify media into a franchise universe" in system:
        content = '{"universe":"none"}'
    elif "extract audiovisual glossary terms" in system:
        content = '{"terms":[]}'
    else:
        content = "[001]\nOlá\n\n[002]\nMundo\n"
    return AIResponse(
        provider_id="mock",
        model_id=model_id,
        content=content,
        input_tokens=8,
        output_tokens=4,
        total_tokens=12,
        actual_cost_usd=Decimal("0"),
        request_id=request_id,
    )


@pytest.fixture
def mock_job_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media = tmp_path / "media" / "Example"
    media.mkdir(parents=True)
    (media / "Example.mkv").write_text("x")
    source = media / "Example.en.srt"
    source.write_text(SAMPLE_SRT, encoding="utf-8")

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path / "media"))
    get_app_config.cache_clear()

    engine = create_engine(f"sqlite:///{config_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import app.db as db_module

    db_module._engine = engine
    db_module._SessionLocal = SessionLocal

    reset_provider_registry()
    fernet = load_or_create_fernet(config_dir / "secret.key")
    db = SessionLocal()
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr.test",
            bazarr_api_key="baz",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            path_mappings=[
                PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path / "media"))
            ],
            routing_strategy="free_only",
        )
    )
    db.add(
        AiModelPreferenceRow(
            provider_id="mock",
            model_id="mock-free",
            tier="free",
            priority=1,
            enabled=True,
        )
    )
    db.commit()

    mock = MockAIProvider(response_factory=_mock_response_factory)
    bootstrap_providers(db)
    get_provider_registry().register(mock)

    yield {
        "db": db,
        "source": source,
        "media": media,
        "mock": mock,
        "SessionLocal": SessionLocal,
    }
    db.close()
    reset_provider_registry()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_create_job_without_openrouter_credentials(mock_job_env):
    db = mock_job_env["db"]
    service = JobService(db)
    job = await service.create_job(
        JobCreate(
            source_subtitle_path=str(mock_job_env["source"]),
            target_language="pt-PT",
            media_type="movie",
            media_path=str(mock_job_env["media"] / "Example.mkv"),
            media_title="Example Movie",
            bazarr_movie_id=10,
            source_language="en",
        )
    )
    assert job.status == "pending"
    assert job.provider_id is None
    row = db.get(JobRow, job.id)
    assert row is not None
    assert row.provider_id is None


@pytest.mark.asyncio
async def test_mock_provider_completes_translation_and_persists_identity(mock_job_env, monkeypatch):
    db = mock_job_env["db"]
    mock = mock_job_env["mock"]
    service = JobService(db)

    async def fake_rescan(self, row):
        return None

    async def fake_verify(self, row):
        return True

    monkeypatch.setattr(JobService, "_rescan", fake_rescan)
    monkeypatch.setattr(JobService, "_verify_target_with_backoff", fake_verify)

    job = await service.create_job(
        JobCreate(
            source_subtitle_path=str(mock_job_env["source"]),
            target_language="pt-PT",
            media_type="movie",
            media_path=str(mock_job_env["media"] / "Example.mkv"),
            media_title="Example Movie",
            bazarr_movie_id=10,
            source_language="en",
        )
    )
    assert job.provider_id is None
    claimed = service.claim_next_job()
    assert claimed is not None
    await service.process_job(claimed.id)

    done = db.get(JobRow, claimed.id)
    assert done is not None
    assert done.status == "completed"
    assert done.provider_id == "mock"
    assert done.model == "mock-free"
    assert any(c.method == "chat_completion" for c in mock.call_history)

    target = mock_job_env["media"] / "Example.pt-PT.srt"
    assert target.exists()
    assert "Olá" in target.read_text(encoding="utf-8")

    usage = list(db.scalars(select(AiUsageRecordRow).where(AiUsageRecordRow.job_id == done.id)).all())
    assert usage
    assert all(u.provider_id == "mock" for u in usage)
    request_ids = [u.request_id for u in usage if u.request_id]
    assert len(request_ids) == len(set(request_ids))

    events = list(
        db.scalars(select(AiRoutingEventRow).where(AiRoutingEventRow.job_id == done.id)).all()
    )
    assert events
    assert any(e.provider_id == "mock" and e.model_id == "mock-free" for e in events)

    cache = db.scalar(
        select(TranslationCacheRow).where(TranslationCacheRow.job_id == done.id)
    )
    assert cache is not None
    assert cache.provider_id == "mock"
    assert cache.model == "mock-free"
