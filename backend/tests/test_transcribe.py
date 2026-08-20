"""Audio transcription source tests (mocked Whisper/OpenAI)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import JobRow, MediaItemRow
from app.jobs.service import JobService
from app.jobs.worker import JOB_KINDS
from app.languages import normalize_language
from app.localization.checkpoints import read_checkpoints
from app.localization.planner import TaskPlanner
from app.localization.service import LocalizationTaskService
from app.media.service import MediaItemService
from app.services.settings import SettingsService
from app.subtitles.transcribe import (
    TranscriptResult,
    TranscriptSegment,
    assess_transcribe_gate,
    filter_segments,
    segments_to_document,
    transcribe_audio,
    transcribe_with_local,
    whisper_cpu_threads,
)


@pytest.fixture
def asr_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media_dir = tmp_path / "media" / "Movie"
    media_dir.mkdir(parents=True)
    video = media_dir / "Film.mkv"
    video.write_bytes(b"fake-video")

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

    fernet = load_or_create_fernet(config_dir / "secret.key")
    db = SessionLocal()
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr.test",
            bazarr_api_key="test-key",
            openrouter_api_key="sk-test",
            openai_api_key="sk-openai",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            asr_provider="local_then_openai",
            asr_local_model="small",
            path_mappings=[
                PathMappingIn(bazarr_prefix="/media", local_prefix=str(tmp_path / "media"))
            ],
        )
    )
    yield db, tmp_path, video
    db.close()
    get_app_config.cache_clear()


def _media(db, path: Path) -> MediaItemRow:
    return MediaItemService(db).upsert_from_candidate_fields(
        media_type="movie",
        title="Film",
        path=str(path),
        bazarr_movie_id=99,
        bazarr_series_id=None,
        bazarr_episode_id=None,
    )


@pytest.mark.asyncio
async def test_gate_false_when_source_srt_exists(asr_env):
    db, tmp_path, video = asr_env
    (video.parent / "Film.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n\n", encoding="utf-8")
    gate = await assess_transcribe_gate(
        str(video),
        media_roots=[str(tmp_path / "media")],
        source_languages=["en"],
        has_active_transcribe=False,
    )
    assert gate.can_transcribe is False
    assert gate.reason_code == "has_source"


@pytest.mark.asyncio
async def test_gate_false_when_other_language_source_srt_exists(asr_env):
    db, tmp_path, video = asr_env
    (video.parent / "Film.fr.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nBonjour\n\n", encoding="utf-8"
    )
    gate = await assess_transcribe_gate(
        str(video),
        media_roots=[str(tmp_path / "media")],
        source_languages=["en"],
        target_language="pt-PT",
        has_active_transcribe=False,
    )
    assert gate.can_transcribe is False
    assert gate.reason_code == "has_source"


@pytest.mark.asyncio
async def test_gate_false_when_extractable(asr_env, monkeypatch):
    db, tmp_path, video = asr_env

    async def fake_probe(_path):
        from app.subtitles.embedded import EmbeddedTrack

        return [
            EmbeddedTrack(
                stream_index=2,
                language="en",
                codec="subrip",
                kind="text",
                extractable=True,
            )
        ]

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    gate = await assess_transcribe_gate(
        str(video),
        media_roots=[str(tmp_path / "media")],
        source_languages=["en"],
        has_active_transcribe=False,
    )
    assert gate.can_transcribe is False
    assert gate.reason_code == "can_extract"


@pytest.mark.asyncio
async def test_gate_false_when_other_language_extractable(asr_env, monkeypatch):
    db, tmp_path, video = asr_env

    async def fake_probe(_path):
        from app.subtitles.embedded import EmbeddedTrack

        return [
            EmbeddedTrack(
                stream_index=3,
                language="fr",
                codec="subrip",
                kind="text",
                extractable=True,
            )
        ]

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    gate = await assess_transcribe_gate(
        str(video),
        media_roots=[str(tmp_path / "media")],
        source_languages=["en"],
        target_language="pt-PT",
        has_active_transcribe=False,
    )
    assert gate.can_transcribe is False
    assert gate.reason_code == "can_extract"


@pytest.mark.asyncio
async def test_gate_true_without_source_or_extract(asr_env, monkeypatch):
    db, tmp_path, video = asr_env

    async def fake_probe(_path):
        return []

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    gate = await assess_transcribe_gate(
        str(video),
        media_roots=[str(tmp_path / "media")],
        source_languages=["en"],
        has_active_transcribe=False,
    )
    assert gate.can_transcribe is True


@pytest.mark.asyncio
async def test_start_transcribe_rejected_when_source_exists(asr_env, monkeypatch):
    db, tmp_path, video = asr_env
    (video.parent / "Film.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n\n", encoding="utf-8")
    media = _media(db, video)

    async def fake_probe(_path):
        return []

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    with pytest.raises(ValueError, match="source subtitle"):
        await JobService(db).start_manual_transcribe(media)


@pytest.mark.asyncio
async def test_start_transcribe_creates_job(asr_env, monkeypatch):
    db, tmp_path, video = asr_env
    media = _media(db, video)

    async def fake_probe(_path):
        return []

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    job = await JobService(db).start_manual_transcribe(media, target_language="pt-PT")
    assert job.job_kind == "transcribe"
    assert job.status == "pending"
    task = LocalizationTaskService(db).find_active(media.id, "pt-PT")
    assert task is not None
    assert task.substate == "transcribing_source"
    assert job.task_id == task.id


@pytest.mark.asyncio
async def test_process_transcribe_then_planner_enqueues_translate(asr_env, monkeypatch):
    db, tmp_path, video = asr_env
    media = _media(db, video)

    async def fake_probe(_path):
        return []

    async def fake_transcribe(media_path, output_path=None, **kwargs):
        from app.subtitles.filenames import build_external_subtitle_path
        from app.subtitles.models import SubtitleBlock, SubtitleDocument
        from app.subtitles.writer.srt import write_srt_atomic

        out = build_external_subtitle_path(media_path, "en")
        write_srt_atomic(
            out,
            SubtitleDocument(
                format="srt",
                encoding="utf-8",
                blocks=[
                    SubtitleBlock(index=1, start="00:00:01,000", end="00:00:02,000", text="Hello"),
                ],
            ),
            overwrite=True,
        )
        return out, TranscriptResult(language="en", segments=[], engine="faster-whisper:small")

    async def fake_present(*_args, **_kwargs):
        return False

    async def fake_rescan(self, row):
        return None

    async def no_candidates(self, **kwargs):
        from app.integrations.bazarr.client import BazarrError

        raise BazarrError("offline")

    created_translate: list[int] = []

    async def fake_create_job(self, payload, **kwargs):
        from app.jobs.service import job_to_out

        job = JobRow(
            task_id=kwargs.get("task_id"),
            job_kind="translate",
            media_type="movie",
            media_path=str(video),
            source_subtitle_path=payload.source_subtitle_path or str(video),
            target_subtitle_path=str(video.parent / "Film.pt.srt"),
            source_language="en",
            target_language="pt-PT",
            model="test",
            status="pending",
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        created_translate.append(job.id)
        return job_to_out(job)

    monkeypatch.setattr("app.subtitles.embedded.probe_subtitle_tracks", fake_probe)
    monkeypatch.setattr("app.jobs.service.transcribe_media_to_srt", fake_transcribe)
    monkeypatch.setattr("app.jobs.service.JobService._rescan", fake_rescan)
    monkeypatch.setattr("app.jobs.service.JobService.create_job", fake_create_job)
    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    monkeypatch.setattr("app.services.candidates.CandidateService.list_candidates", no_candidates)

    created = await JobService(db).start_manual_transcribe(media, target_language="pt-PT")
    row = db.get(JobRow, created.id)
    row.status = "processing"
    db.add(row)
    db.commit()

    await JobService(db).process_job(created.id)
    done = db.get(JobRow, created.id)
    assert done.status == "completed"
    assert Path(done.target_subtitle_path).is_file()
    assert created_translate
    translate = db.get(JobRow, created_translate[0])
    assert translate.source_subtitle_path.endswith(".en.srt")


@pytest.mark.asyncio
async def test_transcribe_matching_target_skips_translate(asr_env, monkeypatch):
    db, tmp_path, video = asr_env
    media = _media(db, video)
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT")
    srt = video.parent / "Film.pt.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n", encoding="utf-8")
    row = JobRow(
        task_id=task.id,
        job_kind="transcribe",
        media_type="movie",
        media_path=str(video),
        media_title="Film",
        source_subtitle_path=str(video),
        target_subtitle_path=str(srt),
        source_language="pt",
        target_language="pt-PT",
        model="faster-whisper:small",
        status="completed",
    )
    db.add(row)
    db.commit()

    async def fake_present(*_args, **_kwargs):
        return True

    monkeypatch.setattr(TaskPlanner, "_bazarr_target_present", fake_present)
    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "completed"
    assert db.scalar(select(JobRow).where(JobRow.job_kind == "translate")) is None
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["source"] == "done"
    assert cps["extract"] == "skipped"
    assert cps["translate"] == "skipped"


@pytest.mark.asyncio
async def test_local_then_openai_fallback(monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")
    calls: list[str] = []

    async def fail_local(*_args, **_kwargs):
        from app.subtitles.transcribe import TranscribeError

        calls.append("local")
        raise TranscribeError("local failed")

    async def ok_openai(*_args, **_kwargs):
        calls.append("openai")
        return TranscriptResult(
            language="en",
            segments=[TranscriptSegment(0.0, 1.0, "Hello")],
            engine="openai:whisper-1",
        )

    monkeypatch.setattr("app.subtitles.transcribe.transcribe_with_local", fail_local)
    monkeypatch.setattr("app.subtitles.transcribe.transcribe_with_openai", ok_openai)
    result = await transcribe_audio(
        audio,
        provider="local_then_openai",
        local_model="small",
        openai_key="sk-test",
    )
    assert result.engine == "openai:whisper-1"
    assert calls == ["local", "openai"]


def test_filter_segments_drops_silence():
    kept = filter_segments(
        [
            TranscriptSegment(0, 1, "  Hello  ", 0.1),
            TranscriptSegment(1, 2, "", 0.0),
            TranscriptSegment(2, 3, "noise", 0.9),
        ]
    )
    assert [item.text for item in kept] == ["Hello"]
    doc = segments_to_document(kept)
    assert doc.blocks[0].start == "00:00:00,000"


def test_claim_transcribe_independent_of_extract(asr_env):
    db, tmp_path, video = asr_env
    db.add(
        JobRow(
            job_kind="extract",
            media_type="movie",
            media_path=str(video),
            source_subtitle_path=str(video),
            target_subtitle_path=str(video.parent / "Film.en.srt"),
            model="ffmpeg-extract",
            status="pending",
        )
    )
    db.add(
        JobRow(
            job_kind="transcribe",
            media_type="movie",
            media_path=str(video),
            source_subtitle_path=str(video),
            target_subtitle_path=str(video.parent / "Film.und.srt"),
            model="faster-whisper:small",
            status="pending",
        )
    )
    db.commit()
    claimed = JobService(db).claim_next_job(job_kind="transcribe")
    assert claimed is not None
    assert claimed.job_kind == "transcribe"
    leftover = db.scalar(select(JobRow).where(JobRow.job_kind == "extract", JobRow.status == "pending"))
    assert leftover is not None


def test_whisper_cpu_threads_leaves_headroom(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.subtitles.transcribe.get_app_config",
        lambda: SimpleNamespace(whisper_cpu_threads=0),
    )
    monkeypatch.setattr("app.subtitles.transcribe.os.cpu_count", lambda: 8)
    assert whisper_cpu_threads() == 4
    monkeypatch.setattr("app.subtitles.transcribe.os.cpu_count", lambda: 2)
    assert whisper_cpu_threads() == 1
    monkeypatch.setattr(
        "app.subtitles.transcribe.get_app_config",
        lambda: SimpleNamespace(whisper_cpu_threads=3),
    )
    assert whisper_cpu_threads() == 3


@pytest.mark.asyncio
async def test_local_whisper_reports_segment_progress(monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")
    seen: list[tuple[float, float]] = []

    def fake_run(*_args, **kwargs):
        callback = kwargs.get("on_progress")
        if callback:
            callback(12.0, 120.0)
            callback(60.0, 120.0)
        return TranscriptResult(
            language="en",
            segments=[TranscriptSegment(0.0, 60.0, "Hi")],
            engine="faster-whisper:tiny",
            duration=120.0,
        )

    monkeypatch.setattr("app.subtitles.transcribe._run_faster_whisper", fake_run)

    async def on_progress(done: float, total: float) -> None:
        seen.append((done, total))

    result = await transcribe_with_local(
        audio,
        model_size="tiny",
        duration=120.0,
        on_progress=on_progress,
    )
    assert result.duration == 120.0
    assert (0.0, 120.0) in seen
    assert any(done == 60.0 and total == 120.0 for done, total in seen)
    assert seen[-1] == (120.0, 120.0)


def test_worker_includes_transcribe_kind():
    assert "transcribe" in JOB_KINDS


def test_normalize_language_for_task():
    assert normalize_language("pt-PT").code == "pt-PT"
