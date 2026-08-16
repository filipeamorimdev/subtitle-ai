"""Bazarr media provider and localization task tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import JobRow, LocalizationTaskRow, MediaItemRow
from app.integrations.bazarr.client import BazarrClient
from app.languages import normalize_language
from app.localization.planner import TaskPlanner
from app.localization.service import (
    ActiveTaskExistsError,
    LocalizationTaskService,
    UnsupportedCapabilityError,
)
from app.localization.state import InvalidTaskTransition, assert_transition
from app.media.bazarr_provider import BazarrMediaProvider, clear_search_cache
from app.media.service import MediaItemService
from app.services.settings import SettingsService


@pytest.fixture
def loc_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media = tmp_path / "media" / "Matrix"
    media.mkdir(parents=True)
    (media / "The Matrix.mkv").write_text("x")
    source = media / "The Matrix.en.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path / "media"))
    get_app_config.cache_clear()

    engine = create_engine(f"sqlite:///{config_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import app.db as db_module

    db_module._engine = engine
    db_module._SessionLocal = SessionLocal

    # Ensure partial unique index exists (create_all may skip it).
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_localization_tasks_active
                ON localization_tasks (media_item_id, target_language_code, capability)
                WHERE status IN (
                    'requested', 'planning', 'waiting_for_source', 'processing', 'verifying'
                )
                """
            )
        )

    fernet = load_or_create_fernet(config_dir / "secret.key")
    db = SessionLocal()
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr.test",
            bazarr_api_key="test-key",
            openrouter_api_key="sk-test",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            path_mappings=[
                PathMappingIn(bazarr_prefix="/media", local_prefix=str(tmp_path / "media"))
            ],
        )
    )
    clear_search_cache()
    yield db, tmp_path, media, source
    db.close()
    get_app_config.cache_clear()


def test_task_state_transitions():
    assert_transition("requested", "planning")
    assert_transition("processing", "verifying")
    assert_transition("verifying", "completed")
    with pytest.raises(InvalidTaskTransition):
        assert_transition("completed", "processing")


def test_create_manual_and_duplicate(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, created = svc.create_manual_task(
        media_item=media,
        target_language="Portuguese (Portugal)",
        capability="subtitles",
    )
    assert created
    assert task.target_language_code == "pt-PT"
    assert task.origin == "manual"
    assert task.priority == "high"

    with pytest.raises(ActiveTaskExistsError) as exc:
        svc.create_manual_task(
            media_item=media,
            target_language="pt-PT",
            capability="subtitles",
        )
    assert exc.value.task_id == task.id

    reused, created2 = svc.ensure_task(
        media_item=media,
        language=normalize_language("pt-PT"),
        origin="automatic",
    )
    assert not created2
    assert reused.id == task.id


def test_completed_does_not_block_new_request(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "completed")

    task2, created = svc.create_manual_task(media_item=media, target_language="pt-PT")
    assert created
    assert task2.id != task.id


def test_unsupported_capability(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/x.mkv",
        bazarr_movie_id=1,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    with pytest.raises(UnsupportedCapabilityError):
        svc.create_manual_task(media_item=media, target_language="pt-PT", capability="audio")


@pytest.mark.asyncio
async def test_cancel_and_retry(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="translating")

    # Pending job should be cancelled with the task.
    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=media.path or "",
        source_subtitle_path=media.path or "",
        target_subtitle_path=(media.path or "") + ".pt-PT.srt",
        model="test",
        status="pending",
    )
    db.add(job)
    db.commit()

    cancelled = svc.cancel(task.id)
    assert cancelled.status == "cancelled"
    db.refresh(job)
    assert job.status == "cancelled"

    retried = svc.prepare_retry(cancelled.id)
    assert retried.status == "planning"


@pytest.mark.asyncio
async def test_planner_completes_when_target_exists(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media_path = media_dir / "The Matrix.mkv"
    target = media_dir / "The Matrix.pt-PT.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")

    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_path),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")

    async def fake_bazarr_present(self, media_row, target_language):
        return True

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_bazarr_present)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "completed"


@pytest.mark.asyncio
async def test_bazarr_media_search_movie_and_episode(monkeypatch):
    clear_search_cache()

    async def fake_request(self, method, path, *, params=None, json_body=None):
        if path == "/api/movies":
            movies = [
                {"radarrId": 1, "title": "The Matrix", "year": 1999, "path": "/m/Matrix.mkv"},
                {"radarrId": 2, "title": "The Matrix Reloaded", "year": 2003},
            ]
            if params and "radarrid[]" in params:
                wanted = {int(x) for x in params["radarrid[]"]}
                return [m for m in movies if int(m["radarrId"]) in wanted]
            return movies
        if path == "/api/series":
            return [{"sonarrSeriesId": 10, "title": "Breaking Bad", "year": 2008}]
        if path == "/api/episodes":
            return [
                {
                    "sonarrEpisodeId": 100,
                    "sonarrSeriesId": 10,
                    "season": 2,
                    "episode": 3,
                    "title": "Four Days Out",
                    "seriesTitle": "Breaking Bad",
                    "path": "/tv/bb.mkv",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(BazarrClient, "_request", fake_request)
    provider = BazarrMediaProvider(BazarrClient("http://bazarr.test", "key"))

    movies = await provider.search_media("Matrix")
    assert any(r.title == "The Matrix" and r.media_type == "movie" for r in movies)

    eps = await provider.search_media("Breaking Bad Four Days")
    assert any(r.media_type == "episode" and r.episode == 3 for r in eps)

    got = await provider.get_media("movie:1")
    assert got is not None
    assert got.title == "The Matrix"

    missing = await provider.get_media("movie:999")
    assert missing is None

    invalid = await provider.get_media("not-an-id")
    assert invalid is None


@pytest.mark.asyncio
async def test_bazarr_provider_error(monkeypatch):
    clear_search_cache()

    async def boom(self, method, path, *, params=None, json_body=None):
        raise httpx.ConnectError("nope")

    # BazarrClient._request wraps HTTPError as BazarrError
    from app.integrations.bazarr.client import BazarrError

    async def fail(self, method, path, *, params=None, json_body=None):
        raise BazarrError("down")

    monkeypatch.setattr(BazarrClient, "_request", fail)
    provider = BazarrMediaProvider(BazarrClient("http://bazarr.test", "key"))
    with pytest.raises(BazarrError):
        await provider.search_media("Matrix")


@pytest.mark.asyncio
async def test_api_languages_and_tasks(loc_env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    db, *_ = loc_env

    async def fake_search(self, query: str):
        from app.media import MediaRef

        return [
            MediaRef(
                provider_id="bazarr",
                external_id="movie:42",
                media_type="movie",
                title="The Matrix",
                year=1999,
                bazarr_movie_id=42,
                path="/media/Matrix/The Matrix.mkv",
            )
        ]

    monkeypatch.setattr(BazarrMediaProvider, "search_media", fake_search)

    async def fake_get(self, external_id: str):
        from app.media import MediaRef

        return MediaRef(
            provider_id="bazarr",
            external_id="movie:42",
            media_type="movie",
            title="The Matrix",
            year=1999,
            bazarr_movie_id=42,
            path="/media/Matrix/The Matrix.mkv",
        )

    monkeypatch.setattr(BazarrMediaProvider, "get_media", fake_get)

    async def fake_present(self, media_row, target_language):
        return False

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)

    async def fake_snapshot(self, media, target_language):
        return {
            "candidate_key": "k",
            "can_translate": False,
            "can_extract": False,
            "can_request": False,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
        }

    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)

    client = TestClient(create_app())
    langs = client.get("/api/languages")
    assert langs.status_code == 200
    assert any(l["code"] == "pt-PT" for l in langs.json())

    search = client.get("/api/media/search", params={"q": "matrix"})
    assert search.status_code == 200
    assert search.json()[0]["title"] == "The Matrix"

    ensure = client.post(
        "/api/media",
        json={
            "external_id": "movie:42",
            "media_type": "movie",
            "title": "The Matrix",
            "bazarr_movie_id": 42,
            "path": "/media/Matrix/The Matrix.mkv",
        },
    )
    assert ensure.status_code == 200
    media_id = ensure.json()["id"]

    created = client.post(
        f"/api/media/{media_id}/localization-tasks",
        json={"target_language": "Português de Portugal", "capability": "subtitles"},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert created.json()["target_language_code"] == "pt-PT"

    dup = client.post(
        f"/api/media/{media_id}/localization-tasks",
        json={"target_language": "pt-PT", "capability": "subtitles"},
    )
    assert dup.status_code == 409
    assert dup.json()["task_id"] == task_id

    audio = client.post(
        f"/api/media/{media_id}/localization-tasks",
        json={"target_language": "pt-PT", "capability": "audio"},
    )
    assert audio.status_code == 422

    detail = client.get(f"/api/localization-tasks/{task_id}")
    assert detail.status_code == 200

    cancel = client.post(f"/api/localization-tasks/{task_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    retry = client.post(f"/api/localization-tasks/{task_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] in {"planning", "waiting_for_source", "processing", "completed"}
