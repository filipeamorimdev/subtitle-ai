"""Bazarr media provider and localization task tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import CandidateOut, PathMappingIn, SettingsUpdate
from app.api.localization_routes import _progress_steps
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
    assert_transition("failed", "completed")
    assert_transition("blocked", "completed")
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


@pytest.mark.asyncio
async def test_audio_task_enqueues_a_dub_job_when_subtitle_is_ready(loc_env):
    db, _tmp_path, media_dir, _source = loc_env
    target_srt = media_dir / "The Matrix.pt-PT.srt"
    target_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá\n", encoding="utf-8")
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, _ = LocalizationTaskService(db).create_manual_task(
        media_item=media,
        target_language="pt-PT",
        capability="audio",
    )

    planned = await TaskPlanner(db).plan(task.id)

    assert planned is not None
    assert planned.status == "processing"
    job = db.scalar(select(JobRow).where(JobRow.task_id == task.id, JobRow.job_kind == "dub"))
    assert job is not None
    assert job.status == "pending"
    assert job.source_subtitle_path == str(target_srt)


def test_audio_task_progress_steps_describe_dubbing(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, _ = LocalizationTaskService(db).create_manual_task(
        media_item=media,
        target_language="pt-PT",
        capability="audio",
    )
    task.status = "processing"
    job = JobRow(
        task_id=task.id,
        job_kind="dub",
        media_path="/media/Matrix/The Matrix.mkv",
        source_subtitle_path="/media/Matrix/The Matrix.pt-PT.srt",
        target_subtitle_path="/media/Matrix/The Matrix.pt-PT.dub.mkv",
        status="processing",
        progress=50,
        progress_detail="Synthesizing cue 5/10",
    )

    steps = _progress_steps(task, [job])

    assert [step["label"] for step in steps] == [
        "Localized subtitles",
        "Preparing voice",
        "Generating speech",
        "Mixing and saving dub",
        "Checking output",
    ]
    assert {step["id"]: step["state"] for step in steps} == {
        "subtitles": "done",
        "voice": "done",
        "speech": "active",
        "mix": "pending",
        "verify": "pending",
    }


def test_subtitle_task_progress_shows_active_transcription_badge(loc_env):
    db, *_ = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path="/media/Matrix/The Matrix.mkv",
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, _ = LocalizationTaskService(db).create_manual_task(
        media_item=media,
        target_language="pt-PT",
        capability="subtitles",
    )
    task.status = "processing"
    task.substate = "transcribing_source"
    job = JobRow(
        task_id=task.id,
        job_kind="transcribe",
        media_path="/media/Matrix/The Matrix.mkv",
        status="processing",
        progress=25,
    )

    steps = _progress_steps(task, [job])

    transcription = next(step for step in steps if step["id"] == "transcribe")
    assert transcription == {
        "id": "transcribe",
        "label": "Transcribing",
        "state": "active",
    }


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


def test_list_tasks_sorts_completed_newest_first(loc_env):
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
    now = datetime.now(timezone.utc)
    oldest, _ = svc.create_manual_task(media_item=media, target_language="en")
    svc.transition(oldest, "completed")
    oldest.completed_at = now - timedelta(days=2)
    db.commit()
    middle, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    svc.transition(middle, "completed")
    middle.completed_at = now - timedelta(hours=3)
    db.commit()
    newest, _ = svc.create_manual_task(media_item=media, target_language="ja-JP")
    svc.transition(newest, "completed")
    newest.completed_at = now
    db.commit()

    rows = svc.list_tasks(status="completed", sort="completed_at", limit=10)
    assert [row.id for row in rows] == [newest.id, middle.id, oldest.id]
    page = svc.list_tasks(status="completed", sort="completed_at", limit=1, offset=1)
    assert [row.id for row in page] == [middle.id]


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
        svc.create_manual_task(media_item=media, target_language="pt-PT", capability="metadata")


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


def test_pause_pending_job_is_not_claimed(loc_env):
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

    from app.jobs.queue import claim_next_job, pause_job_row, resume_job_row

    paused = pause_job_row(db, job.id)
    assert paused.status == "paused"
    assert claim_next_job(db, "translate") is None

    resumed = resume_job_row(db, job.id)
    assert resumed.status == "pending"
    claimed = claim_next_job(db, "translate")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "processing"


@pytest.mark.asyncio
async def test_cancel_marks_processing_jobs_cancelled(loc_env):
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

    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=media.path or "",
        source_subtitle_path=media.path or "",
        target_subtitle_path=(media.path or "") + ".pt-PT.srt",
        model="test",
        status="processing",
        progress_detail="Translating batch 2/8",
    )
    db.add(job)
    db.commit()

    cancelled = svc.cancel(task.id)
    assert cancelled.status == "cancelled"
    db.refresh(job)
    assert job.status == "cancelled"
    assert job.reason_code == "cancelled"
    assert job.progress_detail == "Cancelled with localization task"

    actions = JobService(db).list_job_actions_for_media(media)
    job_actions = [item for item in actions if item.kind == "job"]
    assert any(item.id == job.id and item.status == "cancelled" for item in job_actions)
    cancelled_action = next(item for item in job_actions if item.id == job.id)
    assert cancelled_action.message == "Cancelled with localization task"
    assert any(
        item.kind == "task" and item.id == task.id and item.status == "cancelled" for item in actions
    )


def test_cancel_cancels_unattached_job_for_same_media_language(loc_env):
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

    job = JobRow(
        task_id=None,
        job_kind="translate",
        media_type="movie",
        media_path=media.path or "",
        source_subtitle_path=media.path or "",
        target_subtitle_path=(media.path or "") + ".pt-PT.srt",
        target_language="pt-PT",
        model="test",
        status="pending",
    )
    db.add(job)
    db.commit()

    cancelled = svc.cancel(task.id)
    assert cancelled.status == "cancelled"
    db.refresh(job)
    assert job.status == "cancelled"
    assert job.reason_code == "cancelled"


def test_claim_next_job_aborts_when_task_already_cancelled(loc_env):
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

    job = JobRow(
        task_id=task.id,
        job_kind="translate",
        media_type="movie",
        media_path=media.path or "",
        source_subtitle_path=media.path or "",
        target_subtitle_path=(media.path or "") + ".pt-PT.srt",
        target_language="pt-PT",
        model="test",
        status="pending",
    )
    db.add(job)
    db.commit()

    task.status = "cancelled"
    task.error_code = "cancelled"
    db.add(task)
    db.commit()

    claimed = JobService(db).claim_next_job()
    assert claimed is None
    db.refresh(job)
    assert job.status == "cancelled"
    assert job.reason_code == "cancelled"


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
async def test_planner_completes_failed_task_when_target_exists(loc_env, monkeypatch):
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
    svc.transition(task, "failed", error_code="failed", error_message="Translation failed.")

    async def fake_bazarr_present(self, media_row, target_language):
        return True

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_bazarr_present)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "completed"
    assert planned.error_code is None
    assert planned.error_message is None


@pytest.mark.asyncio
async def test_planner_leaves_failed_task_when_target_missing(loc_env, monkeypatch):
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
    svc.transition(task, "failed", error_code="failed", error_message="Translation failed.")

    async def fake_bazarr_present(self, media_row, target_language):
        return False

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_bazarr_present)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "failed"
    assert planned.error_code == "failed"
    assert not db.scalars(select(JobRow)).all()


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
    assert any(lang["code"] == "pt-PT" for lang in langs.json())

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
    assert audio.status_code == 200
    assert audio.json()["capability"] == "audio"

    metadata = client.post(
        f"/api/media/{media_id}/localization-tasks",
        json={"target_language": "pt-PT", "capability": "metadata"},
    )
    assert metadata.status_code == 422

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
async def test_stale_candidate_falls_back_to_media_bazarr_ids(loc_env, monkeypatch):
    db, _tmp_path, media_dir, _source = loc_env
    media_path = media_dir / "No Local Source.mkv"
    media_path.write_text("x")
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="episode",
        title="No Local Source",
        path=str(media_path),
        bazarr_movie_id=None,
        bazarr_series_id=7,
        bazarr_episode_id=8,
    )
    stale = CandidateOut(
        key="stale-key",
        media_type="episode",
        title=media.title,
        media_path=str(media_path),
        bazarr_episode_id=8,
        bazarr_series_id=None,
        target_language="pt-PT",
        can_translate=False,
        reason_code="no_source",
    )

    async def fake_candidates(self):
        return [stale]

    async def no_extra_resolution(self, result, path, source_languages, target_language):
        return None

    async def no_probe(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.localization.planner.CandidateService.list_candidates", fake_candidates)
    monkeypatch.setattr(TaskPlanner, "_apply_source_resolution", no_extra_resolution)
    monkeypatch.setattr("app.localization.planner.probe_subtitle_tracks", no_probe)

    snapshot = await TaskPlanner(db)._resolve_source_snapshot(media, "pt-PT")

    assert snapshot["candidate_key"] == "stale-key"
    assert snapshot["can_request"] is True


@pytest.mark.asyncio
async def test_source_enqueue_failure_becomes_visible_and_terminal(loc_env, monkeypatch):
    db, _tmp_path, media_dir, _source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="Queue Failure",
        path=str(media_dir / "Queue Failure.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    task, _ = LocalizationTaskService(db).create_manual_task(
        media_item=media,
        target_language="pt-PT",
    )

    async def empty_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "queue-failure",
            "can_translate": False,
            "can_extract": False,
            "can_request": True,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
        }

    async def fail_enqueue(*_args, **_kwargs):
        raise ValueError("Bazarr episode identifier is invalid")

    async def false_present(*_args):
        return False

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", false_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", empty_snapshot)
    monkeypatch.setattr(JobService, "create_request_subtitle_job", fail_enqueue)
    monkeypatch.setattr(JobService, "create_request_subtitle_job_for_media", fail_enqueue)

    planner = TaskPlanner(db)
    first = await planner.plan(task.id)
    assert first is not None and first.status == "waiting_for_source"
    assert "Bazarr episode identifier is invalid" in (first.error_message or "")

    second = await planner.plan(task.id)
    assert second is not None and second.status == "waiting_for_source"

    third = await planner.plan(task.id)
    assert third is not None and third.status == "failed"
    assert third.error_code == "source_enqueue_failed"


@pytest.mark.asyncio
async def test_request_lookup_timeout_does_not_block_worker(loc_env, monkeypatch):
    db, *_ = loc_env
    service = JobService(db)
    row = JobRow(id=901, job_kind="request")

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(service, "_lookup_requested_subtitle", hang)
    monkeypatch.setattr("app.jobs.service.REQUEST_LOOKUP_TIMEOUT_SECONDS", 0.01)

    result = await service._lookup_requested_subtitle_bounded(
        object(), row, "en", []
    )

    assert result is None


def _skipped_request_job(task, media, *, candidate_key="k") -> JobRow:
    return JobRow(
        task_id=task.id,
        candidate_key=candidate_key,
        job_kind="request",
        media_type="movie",
        media_path=media.path or "",
        media_title=media.title,
        source_subtitle_path=media.path or "",
        target_subtitle_path=media.path or "",
        model="bazarr-search",
        status="skipped",
        progress=100,
        progress_detail="No EN subtitle found",
        reason_code="not_found",
        completed_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_manual_task_fails_when_source_search_finds_nothing(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="discovering_source")
    db.add(_skipped_request_job(task, media))
    db.commit()

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

    stopped = await TaskPlanner(db).plan(task.id)
    assert stopped is not None
    assert stopped.status == "failed"
    assert stopped.error_code == "not_found"
    assert stopped.error_message == "No suitable subtitle source was found."
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["source"] == "failed"

    async def source_ready(self, media_row, target_language):
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

    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", source_ready)
    retried = svc.prepare_retry(task.id)
    resumed = await TaskPlanner(db).plan(retried.id)
    assert resumed is not None
    assert resumed.status == "processing"
    jobs = list(db.scalars(select(JobRow).where(JobRow.task_id == task.id, JobRow.job_kind == "translate")).all())
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_automatic_task_waits_when_source_search_finds_nothing(loc_env, monkeypatch):
    db, tmp_path, media_dir, source = loc_env
    media = MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="The Matrix",
        path=str(media_dir / "The Matrix.mkv"),
        bazarr_movie_id=42,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )
    language = normalize_language("pt-PT")
    svc = LocalizationTaskService(db)
    task, _ = svc.ensure_task(media_item=media, language=language, origin="automatic")
    svc.transition(task, "planning")
    task = svc.get(task.id)
    svc.transition(task, "processing", substate="discovering_source")
    db.add(_skipped_request_job(task, media))
    db.commit()

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
    assert waiting.error_code == "not_found"


@pytest.mark.asyncio
async def test_manual_waiting_task_stops_after_source_not_found(loc_env, monkeypatch):
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
    svc.transition(task, "waiting_for_source", substate="awaiting_source")
    db.add(_skipped_request_job(task, media))
    db.commit()

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

    stopped = await TaskPlanner(db).plan(task.id)
    assert stopped is not None
    assert stopped.status == "failed"
    assert stopped.error_code == "not_found"


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

    async def fake_rescan(self, media, target_language):
        return VerificationResult(
            ok=present["ok"],
            present=present["ok"],
            reason_code=None if present["ok"] else "bazarr_verify_failed",
            message=None if present["ok"] else "Target subtitle is not yet visible in Bazarr.",
        )

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", present_after_write)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan)
    after_write = await TaskPlanner(db).plan(task.id)
    assert after_write is not None
    assert after_write.status == "verifying"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["translate"] == "done"
    assert cps["validate"] == "done"
    assert cps["write"] == "done"
    assert cps["sync"] == "active"
    assert cps["verify"] == "failed"

    present["ok"] = True
    done = await TaskPlanner(db).plan(task.id)
    assert done is not None
    assert done.status == "completed"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["sync"] == "done"
    assert cps["verify"] == "done"
    assert source.exists()


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
async def test_extracted_source_removed_after_bazarr_verify(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="translating")
    target = media_dir / "The Matrix.pt.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(media_dir / "The Matrix.mkv"),
            target_subtitle_path=str(source),
            source_language="en",
            model="ffmpeg-extract",
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
            target_subtitle_path=str(target),
            target_language="pt-PT",
            model="mock-free",
            status="completed",
        )
    )
    db.commit()

    present = {"ok": False}
    rescans = {"n": 0}

    async def present_after_write(self, media_row, target_language):
        return present["ok"]

    async def fake_rescan(self, media, target_language):
        return VerificationResult(
            ok=present["ok"],
            present=present["ok"],
            reason_code=None if present["ok"] else "bazarr_verify_failed",
            message=None if present["ok"] else "Target subtitle is not yet visible in Bazarr.",
        )

    async def fake_cleanup_rescan(self, media):
        rescans["n"] += 1

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", present_after_write)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan)
    monkeypatch.setattr(BazarrVerificationService, "rescan", fake_cleanup_rescan)

    after_write = await TaskPlanner(db).plan(task.id)
    assert after_write is not None
    assert after_write.status == "verifying"
    assert source.exists()
    assert target.exists()
    assert rescans["n"] == 0

    present["ok"] = True
    done = await TaskPlanner(db).plan(task.id)
    assert done is not None
    assert done.status == "completed"
    assert not source.exists()
    assert target.exists()
    assert rescans["n"] == 1


@pytest.mark.asyncio
async def test_extracted_source_removed_when_already_verified(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="translating")
    target = media_dir / "The Matrix.pt.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(media_dir / "The Matrix.mkv"),
            target_subtitle_path=str(source),
            source_language="en",
            model="ffmpeg-extract",
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
            target_subtitle_path=str(target),
            target_language="pt-PT",
            model="mock-free",
            status="completed",
        )
    )
    db.commit()

    async def already_present(self, media_row, target_language):
        return True

    async def fake_cleanup_rescan(self, media):
        return None

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", already_present)
    monkeypatch.setattr(BazarrVerificationService, "rescan", fake_cleanup_rescan)

    done = await TaskPlanner(db).plan(task.id)
    assert done is not None
    assert done.status == "completed"
    assert not source.exists()
    assert target.exists()


@pytest.mark.asyncio
async def test_request_extract_fallback_source_removed_after_verify(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="translating")
    target = media_dir / "The Matrix.pt.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="request",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(source),
            source_language="en",
            model="bazarr-search",
            status="completed",
            reason_code="extracted_embedded",
        )
    )
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="translate",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(target),
            target_language="pt-PT",
            model="mock-free",
            status="completed",
        )
    )
    db.commit()

    async def already_present(self, media_row, target_language):
        return True

    async def fake_cleanup_rescan(self, media):
        return None

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", already_present)
    monkeypatch.setattr(BazarrVerificationService, "rescan", fake_cleanup_rescan)

    done = await TaskPlanner(db).plan(task.id)
    assert done is not None
    assert done.status == "completed"
    assert not source.exists()
    assert target.exists()


@pytest.mark.asyncio
async def test_extracted_source_kept_until_bazarr_verify(loc_env, monkeypatch):
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
    svc.transition(task, "verifying", substate="bazarr_sync")
    target = media_dir / "The Matrix.pt.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(media_dir / "The Matrix.mkv"),
            target_subtitle_path=str(source),
            source_language="en",
            model="ffmpeg-extract",
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
            target_subtitle_path=str(target),
            target_language="pt-PT",
            model="mock-free",
            status="completed",
            reason_code="bazarr_verify_failed",
        )
    )
    db.commit()

    async def still_missing(self, media_row, target_language):
        return False

    attempts = {"count": 0}

    async def fake_rescan(self, media, target_language):
        attempts["count"] += 1
        return VerificationResult(
            ok=False,
            present=False,
            reason_code="bazarr_verify_failed",
            message="Target subtitle is not yet visible in Bazarr.",
        )

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", still_missing)
    monkeypatch.setattr(BazarrVerificationService, "rescan_and_verify", fake_rescan)

    still = await TaskPlanner(db).plan(task.id)
    assert still is not None
    assert still.status == "verifying"
    assert still.metadata_json["verify_retry"]["attempts"] == 1
    assert source.exists()
    assert target.exists()

    # The periodic planner sees the active task again, but persisted backoff
    # prevents it from hammering Bazarr every replan interval.
    await TaskPlanner(db).plan(task.id)
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_plan_after_extract_translates_when_wanted_snapshot_is_stale(loc_env, monkeypatch):
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
            source_subtitle_path=str(media_dir / "The Matrix.mkv"),
            target_subtitle_path=str(source),
            source_language="en",
            model="tesseract-ocr",
            status="completed",
        )
    )
    db.commit()

    created: list[str] = []

    async def fake_present(self, media_row, target_language):
        return False

    async def stale_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": False,
            "can_extract": True,
            "can_request": True,
            "source_path": None,
            "source_language": "en",
            "extract_stream_index": 2,
            "target_exists": False,
        }

    async def fake_create_job(self, payload, **kwargs):
        from app.jobs.service import job_to_out

        created.append("translate")
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
        created.append("extract")
        raise AssertionError("completed extract must not be queued again")

    async def fake_create_request(self, *args, **kwargs):
        created.append("request")
        raise AssertionError("completed extract must not fall back to request")

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", stale_snapshot)
    monkeypatch.setattr("app.jobs.service.JobService.create_job", fake_create_job)
    monkeypatch.setattr("app.jobs.service.JobService.create_extract_job", fake_create_extract)
    monkeypatch.setattr(
        "app.jobs.service.JobService.create_request_subtitle_job", fake_create_request
    )

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "processing"
    assert planned.substate == "translating"
    assert created == ["translate"]


@pytest.mark.asyncio
async def test_plan_after_request_translates_when_wanted_snapshot_is_stale(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="discovering_source")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="request",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path=str(source),
            target_subtitle_path=str(source),
            source_language="en",
            target_language="en",
            model="bazarr-search",
            status="completed",
        )
    )
    db.commit()

    created: list[str] = []

    async def fake_present(self, media_row, target_language):
        return False

    async def stale_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": False,
            "can_extract": False,
            "can_request": True,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
        }

    async def fake_create_job(self, payload, **kwargs):
        from app.jobs.service import job_to_out

        created.append("translate")
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

    async def fake_create_request(self, *args, **kwargs):
        created.append("request")
        raise AssertionError("completed request must not be queued again")

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", stale_snapshot)
    monkeypatch.setattr("app.jobs.service.JobService.create_job", fake_create_job)
    monkeypatch.setattr(
        "app.jobs.service.JobService.create_request_subtitle_job", fake_create_request
    )

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "processing"
    assert planned.substate == "translating"
    assert created == ["translate"]


@pytest.mark.asyncio
async def test_plan_after_completed_request_without_source_does_not_loop(loc_env, monkeypatch):
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
    svc.transition(task, "processing", substate="discovering_source")
    db.add(
        JobRow(
            task_id=task.id,
            job_kind="request",
            media_type="movie",
            media_path=str(media_dir / "The Matrix.mkv"),
            source_subtitle_path="/movies/Missing.en.srt",
            target_subtitle_path="/movies/Missing.en.srt",
            source_language="en",
            target_language="en",
            model="bazarr-search",
            status="completed",
        )
    )
    db.commit()

    created: list[str] = []

    async def fake_present(self, media_row, target_language):
        return False

    async def stale_snapshot(self, media_row, target_language):
        return {
            "candidate_key": "k",
            "can_translate": False,
            "can_extract": False,
            "can_request": True,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
        }

    async def fake_create_request(self, *args, **kwargs):
        created.append("request")
        raise AssertionError("completed request must not be queued again")

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr(TaskPlanner, "_resolve_source_snapshot", stale_snapshot)
    monkeypatch.setattr(
        "app.jobs.service.JobService.create_request_subtitle_job", fake_create_request
    )

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "failed"
    assert planned.error_code == "not_found"
    assert created == []


def test_latest_task_does_not_overlay_regional_onto_generic_chip(loc_env):
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
    svc.transition(task, "processing", substate="extracting_source")

    assert svc.latest_task_for_language(media.id, "pt-PT").id == task.id
    assert svc.latest_task_for_language(media.id, "pt") is None
    assert svc.latest_task_for_language(media.id, "pt-BR") is None


def test_latest_task_generic_portuguese_does_not_overlay_regional_chips(loc_env):
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
    task, _ = svc.create_manual_task(media_item=media, target_language="pt")
    cancelled = svc.cancel(task.id)
    assert cancelled.status == "cancelled"

    assert svc.latest_task_for_language(media.id, "pt").id == task.id
    assert svc.latest_task_for_language(media.id, "pt-PT") is None
    assert svc.latest_task_for_language(media.id, "pt-BR") is None


def test_language_chip_matches_task_is_one_way():
    from app.subtitles.filenames import language_chip_matches_task

    assert language_chip_matches_task("pt-PT", "pt-PT")
    assert language_chip_matches_task("pt", "pt")
    assert language_chip_matches_task("pt", "pt-PT")
    assert not language_chip_matches_task("pt-PT", "pt")
    assert not language_chip_matches_task("pt-PT", "pt-BR")


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
async def test_written_translate_without_verify_reason_still_rescans(loc_env, monkeypatch):
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
            reason_code="markup_warning",
            warning="markup tags restored with fallback",
        )
    )
    db.commit()

    calls = {"n": 0}

    async def fake_present(self, media_row, target_language):
        return False

    async def fake_rescan(self, media, target_language):
        calls["n"] += 1
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
    assert planned.error_code == "bazarr_verify_failed"
    assert calls["n"] == 1
    job = db.scalars(select(JobRow).where(JobRow.task_id == task.id)).one()
    assert job.reason_code == "markup_warning"


def test_worker_does_not_cancel_completed_inflight_jobs(loc_env):
    db, tmp_path, media_dir, source = loc_env
    from app.jobs.worker import JobWorker

    row = JobRow(
        job_kind="translate",
        media_type="movie",
        media_path=str(media_dir / "The Matrix.mkv"),
        source_subtitle_path=str(source),
        target_subtitle_path=str(media_dir / "The Matrix.pt-PT.srt"),
        model="test",
        status="completed",
        reason_code="markup_warning",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    cancelled_row = JobRow(
        job_kind="translate",
        media_type="movie",
        media_path=str(media_dir / "The Matrix.mkv"),
        source_subtitle_path=str(source),
        target_subtitle_path=str(media_dir / "The Matrix.pt-PT.srt"),
        model="test",
        status="cancelled",
        reason_code="cancelled",
    )
    db.add(cancelled_row)
    db.commit()
    db.refresh(cancelled_row)

    worker = JobWorker()

    class _FakeTask:
        def __init__(self) -> None:
            self._cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self._cancelled = True

        def cancelled(self) -> bool:
            return self._cancelled

    completed_task = _FakeTask()
    cancelled_task = _FakeTask()
    worker._tasks_by_job[row.id] = completed_task  # type: ignore[assignment]
    worker._tasks_by_job[cancelled_row.id] = cancelled_task  # type: ignore[assignment]
    worker._reconcile_cancelled_slots()
    assert not completed_task.cancelled()
    assert cancelled_task.cancelled()


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
            task_id=task.id,
            job_kind="extract",
            media_type="movie",
            media_path=media.path or "",
            media_title=media.title,
            bazarr_movie_id=42,
            source_subtitle_path="",
            target_subtitle_path="/media/Matrix/The Matrix.en.srt",
            target_language="en",
            model="",
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
    assert "translate" in kinds
    assert "extract" in kinds
    assert "request" in kinds
    assert "localize" in kinds
    assert "pt-PT" in langs
    assert "en" in langs
    assert any(item.kind == "task" and item.current for item in actions)

    translate = next(item for item in actions if item.action == "translate")
    extract = next(item for item in actions if item.action == "extract")
    request = next(item for item in actions if item.action == "request")
    localize = next(item for item in actions if item.action == "localize")
    assert translate.related_job_id == translate.id
    assert translate.model == "test"
    assert extract.related_job_id is None
    assert extract.model is None
    assert request.related_job_id is None
    assert request.model is None
    assert localize.related_job_id == translate.id
    assert localize.model is None


def test_list_job_actions_for_media_includes_cancelled_task_without_cancelled_job(loc_env):
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
    svc.transition(task, "verifying", substate="bazarr_verify")

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
    db.commit()

    cancelled = svc.cancel(task.id)
    assert cancelled.status == "cancelled"

    actions = JobService(db).list_job_actions_for_media(media)
    assert any(item.kind == "job" and item.status == "completed" for item in actions)
    localize = next(item for item in actions if item.kind == "task" and item.id == task.id)
    assert localize.status == "cancelled"
    translate = next(item for item in actions if item.action == "translate")
    assert localize.related_job_id == translate.id


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
    assert row.related_job_id is None
