"""Bazarr media provider and localization task tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import AiUsageRecordRow, JobRow, LocalizationTaskRow, MediaItemRow
from app.integrations.bazarr.client import BazarrClient
from app.languages import LanguageNormalizationError, normalize_language
from app.localization.planner import TaskPlanner
from app.localization.service import (
    ActiveTaskExistsError,
    LocalizationTaskService,
    UnsupportedCapabilityError,
)
from app.localization.state import InvalidTaskTransition, assert_transition
from app.localization.checkpoints import read_checkpoints
from app.localization.verification import BazarrVerificationService, VerificationResult
from app.media import MediaRef
from app.media.bazarr_provider import BazarrMediaProvider, clear_search_cache
from app.media.service import MediaItemService
from app.jobs.service import JobService
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

    engine = create_engine(
        f"sqlite:///{config_dir / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
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


def test_media_upsert_idempotent(loc_env):
    db, *_ = loc_env
    svc = MediaItemService(db)
    first = svc.upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    second = svc.upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(MediaItemRow)) == 1


def test_media_parent_child(loc_env):
    db, *_ = loc_env
    svc = MediaItemService(db)
    from app.media import MediaRef

    series = svc.upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="series:10",
            media_type="series",
            title="Breaking Bad",
            bazarr_series_id=10,
        )
    )
    episode = svc.upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="episode:100",
            media_type="episode",
            title="Breaking Bad - S02E03",
            parent_external_id="series:10",
            bazarr_series_id=10,
            bazarr_episode_id=100,
            season=2,
            episode=3,
        )
    )
    assert episode.parent_media_id == series.id


@pytest.mark.asyncio
async def test_search_cache_ttl(monkeypatch):
    clear_search_cache()
    calls = {"movies": 0}

    async def fake_request(self, method, path, *, params=None, json_body=None):
        if path == "/api/movies":
            calls["movies"] += 1
            return [{"radarrId": 1, "title": "The Matrix", "year": 1999}]
        if path == "/api/series":
            return []
        if path == "/api/episodes":
            return []
        raise AssertionError(path)

    monkeypatch.setattr(BazarrClient, "_request", fake_request)
    provider = BazarrMediaProvider(BazarrClient("http://bazarr.test", "key"))
    first = await provider.search_media("Matrix")
    second = await provider.search_media("Matrix")
    assert first[0].title == "The Matrix"
    assert second[0].title == first[0].title
    assert calls["movies"] == 1
    clear_search_cache()
    await provider.search_media("Matrix")
    assert calls["movies"] == 2


@pytest.mark.asyncio
async def test_planner_plan_is_idempotent(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": True,
            "can_extract": False,
            "can_request": False,
            "source_path": str(source),
            "source_language": "en",
            "extract_stream_index": None,
            "target_exists": False,
        }

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)

    planner = TaskPlanner(db)
    await planner.plan(task.id)
    await planner.plan(task.id)
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    active = [j for j in jobs if j.status in {"pending", "processing"}]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_planner_completed_creates_no_work(loc_env, monkeypatch):
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
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "completed")
    await TaskPlanner(db).plan(task.id)
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    assert jobs == []


@pytest.mark.asyncio
async def test_planner_cancel_prevents_future_work(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.cancel(task.id)

    async def fake_snapshot(self, media_row, target_language):
        raise AssertionError("cancelled task must not resolve sources")

    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "cancelled"
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    assert jobs == []


@pytest.mark.asyncio
async def test_verification_failure_does_not_complete(loc_env, monkeypatch):
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
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="translating")

    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=str(media_path),
        source_subtitle_path=str(source),
        target_subtitle_path=str(target),
        model="test",
        status="completed",
        reason_code="bazarr_verify_failed",
        warning="still missing",
    )
    db.add(job)
    db.commit()

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_rescan_and_verify(self, media, target_language):
        return VerificationResult(
            ok=False,
            present=False,
            reason_code="bazarr_verify_failed",
            message="Target subtitle is not yet visible in Bazarr.",
        )

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan_and_verify)

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "verifying"
    assert planned.status != "completed"


@pytest.mark.asyncio
async def test_typed_japanese_creates_task(loc_env, monkeypatch):
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
    task, created = svc.create_manual_task(media_item=media, target_language="ja-JP")
    assert created
    assert task.target_language_code == "ja-JP"
    assert "Japanese" in task.target_language_name


def test_active_task_unique_constraint(loc_env):
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
    db.add(
        LocalizationTaskRow(
            media_item_id=media.id,
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            capability="subtitles",
            status="processing",
            origin="manual",
            priority="high",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(LocalizationTaskRow)) == 1
    assert svc.find_active(media.id, "pt-PT").id == task.id


@pytest.mark.asyncio
async def test_waiting_task_resumes_when_source_appears(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    calls = {"n": 0}

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_snapshot(self, media_row, target_language):
        calls["n"] += 1
        if calls["n"] == 1:
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
        return {
            "candidate_key": "k",
            "can_translate": True,
            "can_extract": False,
            "can_request": False,
            "source_path": str(source),
            "source_language": "en",
            "extract_stream_index": None,
            "target_exists": False,
        }

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)

    planner = TaskPlanner(db)
    waiting = await planner.plan(task.id)
    assert waiting is not None
    assert waiting.status == "waiting_for_source"

    resumed = await planner.plan(task.id)
    assert resumed is not None
    assert resumed.status == "processing"
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    assert len(jobs) == 1
    assert jobs[0].job_kind == "translate"


@pytest.mark.asyncio
async def test_retry_verification_does_not_retranslate(loc_env, monkeypatch):
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
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="translating")

    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=str(media_path),
        source_subtitle_path=str(source),
        target_subtitle_path=str(target),
        model="test",
        status="completed",
        reason_code="bazarr_verify_failed",
        warning="still missing",
    )
    db.add(job)
    db.commit()

    created: list[object] = []

    async def fake_create_job(self, payload, **kwargs):
        created.append(payload)
        raise AssertionError("verification retry must not enqueue translation")

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_rescan_and_verify(self, media, target_language):
        return VerificationResult(
            ok=False,
            present=False,
            reason_code="bazarr_verify_failed",
            message="Target subtitle is not yet visible in Bazarr.",
        )

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr("app.jobs.service.JobService.create_job", fake_create_job)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan_and_verify)

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "verifying"
    assert created == []
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    assert len(jobs) == 1
    assert jobs[0].job_kind == "translate"


def test_task_cost_aggregates_retries(loc_env):
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
    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=media.path or "",
        source_subtitle_path=media.path or "",
        target_subtitle_path=(media.path or "") + ".pt-PT.srt",
        model="mock-free",
        provider_id="mock",
        status="failed",
    )
    db.add(job)
    db.commit()
    db.add(
        AiUsageRecordRow(
            job_id=job.id,
            operation_type="translation",
            provider_id="mock",
            model_id="mock-free",
            request_id="req-1",
            actual_cost_micro_usd=2000,
            total_tokens=100,
        )
    )
    db.add(
        AiUsageRecordRow(
            job_id=job.id,
            operation_type="translation",
            provider_id="mock",
            model_id="mock-free",
            request_id="req-2",
            actual_cost_micro_usd=3000,
            total_tokens=150,
        )
    )
    db.commit()
    summary = svc.ai_summary(task.id)
    assert summary["requests"] == 2
    assert summary["tokens"] == 250
    assert summary["cost_usd"] == pytest.approx(0.005)
    assert summary["provider_id"] == "mock"
    assert summary["model_id"] == "mock-free"


def test_new_task_initializes_checkpoints(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/x.mkv",
        bazarr_movie_id=1,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, _ = LocalizationTaskService(db).create_manual_task(
        media_item=media, target_language="pt-PT"
    )
    cps = read_checkpoints(task.metadata_json)
    assert cps == {
        "source": "pending",
        "extract": "pending",
        "translate": "pending",
        "validate": "pending",
        "write": "pending",
        "sync": "pending",
        "verify": "pending",
    }


@pytest.mark.asyncio
async def test_checkpoint_external_source_then_write_then_verify(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": True,
            "can_extract": False,
            "can_request": False,
            "source_path": str(source),
            "source_language": "en",
            "extract_stream_index": None,
            "target_exists": False,
        }

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["source"] == "done"
    assert cps["extract"] == "skipped"
    assert cps["translate"] == "active"
    assert cps["validate"] == "pending"
    assert cps["write"] == "pending"
    assert cps["sync"] == "pending"
    assert cps["verify"] == "pending"

    target = media_dir / "The Matrix.pt-PT.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    job = db.scalars(select(JobRow).where(JobRow.task_id == task.id)).one()
    job.status = "completed"
    job.target_subtitle_path = str(target)
    job.reason_code = None
    db.add(job)
    db.commit()

    present = {"ok": False}

    async def present_after_write(self, media_row, target_language):
        return present["ok"]

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", present_after_write)
    after_write = await TaskPlanner(db).plan(task.id)
    assert after_write is not None
    assert after_write.status == "verifying"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["translate"] == "done"
    assert cps["validate"] == "done"
    assert cps["write"] == "done"
    assert cps["sync"] == "active"
    assert cps["verify"] == "active"

    present["ok"] = True
    done = await TaskPlanner(db).plan(task.id)
    assert done is not None
    assert done.status == "completed"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["sync"] == "done"
    assert cps["verify"] == "done"


@pytest.mark.asyncio
async def test_checkpoint_embedded_extract_then_translate(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    phase = {"n": 0}

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_snapshot(self, media_row, target_language):
        if phase["n"] == 0:
            return {
                "candidate_key": "k",
                "can_translate": False,
                "can_extract": True,
                "can_request": False,
                "source_path": None,
                "source_language": "en",
                "extract_stream_index": 2,
                "target_exists": False,
            }
        return {
            "candidate_key": "k",
            "can_translate": True,
            "can_extract": False,
            "can_request": False,
            "source_path": str(source),
            "source_language": "en",
            "extract_stream_index": None,
            "target_exists": False,
        }

    created_extract: list[object] = []

    async def fake_create_extract(self, payload, **kwargs):
        from app.jobs.service import job_to_out

        row = JobRow(
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(source),
            model="",
            status="pending",
            extract_stream_index=2,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        created_extract.append(row.id)
        return job_to_out(row)

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)
    monkeypatch.setattr("app.jobs.service.JobService.create_extract_job", fake_create_extract)

    extracting = await TaskPlanner(db).plan(task.id)
    assert extracting is not None
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["source"] == "done"
    assert cps["extract"] == "active"
    assert created_extract

    extract_row = db.get(JobRow, created_extract[0])
    extract_row.status = "completed"
    db.add(extract_row)
    db.commit()
    phase["n"] = 1

    translating = await TaskPlanner(db).plan(task.id)
    assert translating is not None
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["extract"] == "done"
    assert cps["translate"] == "active"


@pytest.mark.asyncio
async def test_verification_failure_marks_verify_failed_not_completed(loc_env, monkeypatch):
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
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="translating")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="translate",
            media_type="movie",
            media_path=str(media_path),
            source_subtitle_path=str(source),
            target_subtitle_path=str(target),
            model="test",
            status="completed",
            reason_code="bazarr_verify_failed",
            warning="still missing",
        )
    )
    db.commit()

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_rescan(self, media, target_language):
        return VerificationResult(
            ok=False,
            present=False,
            reason_code="bazarr_verify_failed",
            message="Target subtitle is not yet visible in Bazarr.",
        )

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan)

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "verifying"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["verify"] == "failed"


@pytest.mark.asyncio
async def test_retry_translate_reuses_source_and_extract(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="extracting_source")

    db.add(
        JobRow(
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(source),
            model="",
            status="completed",
        )
    )
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="translate",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(media_dir / "The Matrix.pt-PT.srt"),
            model="mock-free",
            status="failed",
            reason_code="provider_error",
        )
    )
    db.commit()
    svc.transition(svc.get(task.id), "failed", error_code="provider_error")
    retried = svc.prepare_retry(task.id)
    assert retried.status == "planning"

    created_kinds: list[str] = []

    async def fake_create_job(self, payload, **kwargs):
        from app.jobs.service import job_to_out

        created_kinds.append("translate")
        row = JobRow(
            task_id=task.id,
            job_kind="translate",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(media_dir / "The Matrix.pt-PT.srt"),
            model="",
            provider_id=None,
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    async def fake_create_extract(self, payload, **kwargs):
        created_kinds.append("extract")
        raise AssertionError("retry must not extract again")

    async def fake_create_request(self, *args, **kwargs):
        created_kinds.append("request")
        raise AssertionError("retry must not request source again")

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": True,
            "can_extract": True,
            "can_request": True,
            "source_path": str(source),
            "source_language": "en",
            "extract_stream_index": 2,
            "target_exists": False,
        }

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)
    monkeypatch.setattr("app.jobs.service.JobService.create_job", fake_create_job)
    monkeypatch.setattr("app.jobs.service.JobService.create_extract_job", fake_create_extract)
    monkeypatch.setattr(
        "app.jobs.service.JobService.create_request_subtitle_job", fake_create_request
    )

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "processing"
    assert created_kinds == ["translate"]
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id)).all())
    assert sum(1 for j in jobs if j.job_kind == "extract") == 1
    assert sum(1 for j in jobs if j.job_kind == "translate") == 2
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["source"] == "done"
    assert cps["extract"] == "done"
    assert cps["translate"] == "active"


@pytest.mark.asyncio
async def test_cancel_does_not_advance_checkpoints(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.update_checkpoints(task.id, source="done", extract="skipped", translate="active")
    svc.cancel(task.id)

    async def boom(self, media_row, target_language):
        raise AssertionError("cancelled task must not plan work")

    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", boom)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "cancelled"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["translate"] == "active"
    assert cps["verify"] != "done"


@pytest.mark.parametrize(
    "raw,code",
    [
        ("Português de Portugal", "pt-PT"),
        ("Portuguese (Portugal)", "pt-PT"),
        ("pt-PT", "pt-PT"),
        ("Português do Brasil", "pt-BR"),
        ("Portuguese (Brazil)", "pt-BR"),
        ("pt-BR", "pt-BR"),
        ("pt", "pt"),
        ("Japanese", "ja"),
        ("ja", "ja"),
        ("ja-JP", "ja-JP"),
        ("Japanese (Japan)", "ja-JP"),
        ("Korean", "ko"),
        ("ko-KR", "ko-KR"),
        ("Korean/Korea", "ko-KR"),
        ("Dutch", "nl"),
        ("nl-NL", "nl-NL"),
        ("Polish", "pl"),
        ("pl-PL", "pl-PL"),
    ],
)
def test_language_input_becomes_task_code(loc_env, raw, code):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/x.mkv",
        bazarr_movie_id=1,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, created = LocalizationTaskService(db).create_manual_task(
        media_item=media, target_language=raw
    )
    assert created
    assert task.target_language_code == code
    LocalizationTaskService(db).transition(task, "planning")
    LocalizationTaskService(db).transition(
        LocalizationTaskService(db).get(task.id), "completed"
    )


def test_invalid_language_rejected(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/x.mkv",
        bazarr_movie_id=1,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    with pytest.raises(LanguageNormalizationError):
        LocalizationTaskService(db).create_manual_task(
            media_item=media, target_language="xx-UNKNOWN"
        )
    assert db.scalar(select(func.count()).select_from(LocalizationTaskRow)) == 0


@pytest.mark.asyncio
async def test_api_language_and_invalid_and_pagination(loc_env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )

    async def fake_present(self, media_row, target_language):
        return False

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

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", fake_snapshot)

    client = TestClient(create_app())
    ja = client.post(
        f"/api/media/{media.id}/localization-tasks",
        json={"target_language": "ja-JP", "capability": "subtitles"},
    )
    assert ja.status_code == 200
    assert ja.json()["target_language_code"] == "ja-JP"

    bad = client.post(
        f"/api/media/{media.id}/localization-tasks",
        json={"target_language": "not-a-real-language-zzz", "capability": "subtitles"},
    )
    assert bad.status_code == 400

    br = client.post(
        f"/api/media/{media.id}/localization-tasks",
        json={"target_language": "Português do Brasil", "capability": "subtitles"},
    )
    assert br.status_code == 200
    assert br.json()["target_language_code"] == "pt-BR"

    listed = client.get("/api/localization-tasks", params={"limit": 1, "offset": 0})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert int(listed.headers["X-Total-Count"]) >= 2


def test_concurrent_duplicate_returns_existing_id(loc_env):
    from concurrent.futures import ThreadPoolExecutor

    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    SessionLocal = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    results: list[tuple[int, bool] | int] = []

    def worker() -> None:
        session = SessionLocal()
        try:
            item = session.get(MediaItemRow, media.id)
            assert item is not None
            svc = LocalizationTaskService(session)
            try:
                task, created = svc.create_manual_task(
                    media_item=item, target_language="pt-PT"
                )
                results.append((task.id, created))
            except ActiveTaskExistsError as exc:
                results.append(exc.task_id)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))

    ids = {item[0] if isinstance(item, tuple) else item for item in results}
    assert len(ids) == 1
    existing_id = next(iter(ids))
    assert db.scalar(select(func.count()).select_from(LocalizationTaskRow)) == 1
    created_flags = [item[1] for item in results if isinstance(item, tuple)]
    assert True in created_flags or any(not isinstance(item, tuple) for item in results)
    assert all(
        (item[0] if isinstance(item, tuple) else item) == existing_id for item in results
    )


@pytest.mark.asyncio
async def test_search_cache_independent_queries(monkeypatch):
    clear_search_cache()
    calls = {"movies": 0}

    async def fake_request(self, method, path, *, params=None, json_body=None):
        if path == "/api/movies":
            calls["movies"] += 1
            return [
                {"radarrId": 1, "title": "The Matrix", "year": 1999},
                {"radarrId": 2, "title": "Dune", "year": 2021},
            ]
        if path == "/api/series":
            return []
        if path == "/api/episodes":
            return []
        raise AssertionError(path)

    monkeypatch.setattr(BazarrClient, "_request", fake_request)
    provider = BazarrMediaProvider(BazarrClient("http://bazarr.test", "key"))
    await provider.search_media("Matrix")
    await provider.search_media("Dune")
    assert calls["movies"] == 2
    await provider.search_media("Matrix")
    assert calls["movies"] == 2
    clear_search_cache()


def test_search_upsert_upsert_same_media_id(loc_env):
    db, *_ = loc_env
    from app.media import MediaRef

    svc = MediaItemService(db)
    ref = MediaRef(
        provider_id="bazarr",
        external_id="movie:42",
        media_type="movie",
        title="The Matrix",
        year=1999,
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
    )
    first = svc.upsert_from_ref(ref)
    second = svc.upsert_from_ref(ref)
    third = svc.upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="movie:42",
            media_type="movie",
            title="The Matrix (remaster)",
            year=1999,
            bazarr_movie_id=42,
        )
    )
    assert first.id == second.id == third.id
    assert db.scalar(select(func.count()).select_from(MediaItemRow)) == 1


@pytest.mark.asyncio
async def test_waiting_vs_blocked_messages(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")

    async def fake_present(self, media_row, target_language):
        return False

    async def empty_snapshot(self, media_row, target_language):
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

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", empty_snapshot)
    waiting = await TaskPlanner(db).plan(task.id)
    assert waiting is not None
    assert waiting.status == "waiting_for_source"
    assert waiting.error_message == "Waiting for a subtitle source."

    orphan = MediaItemService(db).upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="movie:99",
            media_type="movie",
            title="No Ref",
        )
    )
    blocked_task, _ = svc.create_manual_task(media_item=orphan, target_language="pt-PT")
    blocked = await TaskPlanner(db).plan(blocked_task.id)
    assert blocked is not None
    assert blocked.status == "blocked"
    assert blocked.error_message == "No usable media reference is available."


def test_list_job_actions_for_media_includes_legacy_and_task_jobs(loc_env):
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
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="translate",
            media_type="movie",
            media_path=media.path or "",
            media_title=media.title,
            bazarr_movie_id=42,
            source_subtitle_path="/media/Matrix/The Matrix.en.srt",
            target_subtitle_path="/media/Matrix/The Matrix.pt-PT.srt",
            target_language="pt-PT",
            model="test",
            status="completed",
        )
    )
    db.add(
        JobRow(
            job_kind="request",
            media_type="movie",
            media_path=media.path or "",
            media_title=media.title,
            bazarr_movie_id=42,
            source_subtitle_path="",
            target_subtitle_path="",
            target_language="en",
            model="",
            status="completed",
        )
    )
    db.commit()

    actions = JobService(db).list_job_actions_for_media(media)
    kinds = {item.action for item in actions}
    langs = {item.target_language for item in actions}
    assert kinds == {"translate", "request"}
    assert "pt-PT" in langs
    assert "en" in langs
    assert all(item.kind == "job" for item in actions)


def test_list_job_actions_for_media_includes_tasks_without_jobs(loc_env):
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
    task, _ = svc.create_manual_task(media_item=media, target_language="de")

    actions = JobService(db).list_job_actions_for_media(media)
    assert len(actions) == 1
    row = actions[0]
    assert row.kind == "task"
    assert row.action == "localize"
    assert row.target_language == "de"
    assert row.id == task.id
