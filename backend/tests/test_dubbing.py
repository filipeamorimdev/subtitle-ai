"""TTS dub preview tests (mocked Piper/ffmpeg)."""

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
from app.jobs.event_log import job_event_log_path
from app.jobs.service import JobService
from app.jobs.worker import JOB_KINDS
from app.localization.checkpoints import read_checkpoints
from app.localization.planner import TaskPlanner
from app.localization.service import LocalizationTaskService
from app.media.service import MediaItemService
from app.services.settings import SettingsService
from app.subtitles.filenames import build_dub_preview_path
from app.dubbing.dub import (
    _piper_voice_download_urls,
    build_mux_command,
    build_tts_mix_command,
)


def test_piper_voice_download_urls_encode_unicode():
    _voice_code, model_url, config_url = _piper_voice_download_urls("pt_PT-tugão-medium")
    assert "tug%C3%A3o" in model_url
    assert model_url.endswith(".onnx?download=true")
    assert config_url.endswith(".onnx.json?download=true")


def test_build_tts_mix_command_mixes_prepositioned_clips(tmp_path):
    clip_a = tmp_path / "a.wav"
    clip_b = tmp_path / "b.wav"
    clip_a.write_bytes(b"a")
    clip_b.write_bytes(b"b")
    out = tmp_path / "mix.wav"

    cmd = build_tts_mix_command(
        [(clip_a, 0), (clip_b, 0)],
        out,
        media_duration_s=1_428.0,
    )
    joined = " ".join(cmd)

    assert "adelay" not in joined
    assert "-itsoffset" not in joined
    assert "normalize=0" in joined
    assert "volume=18.0dB" in joined
    assert "alimiter" in joined
    assert "apad=whole_dur=1428.000" in joined
    assert "-ar 48000" in joined
    assert str(clip_a) in joined and str(clip_b) in joined


def test_build_mux_command_copies_original_audio(tmp_path):
    media = tmp_path / "film.mkv"
    tts = tmp_path / "tts.wav"
    out = tmp_path / "out.mkv"
    media.write_bytes(b"m")
    tts.write_bytes(b"t")

    cmd = build_mux_command(
        media,
        tts,
        out,
        lang_tag="por",
        copy_original_audio=True,
    )

    assert "-c:a:0" in cmd and "copy" in cmd[cmd.index("-c:a:0") + 1]
    assert "-c:a:1" in cmd and "aac" in cmd[cmd.index("-c:a:1") + 1]
    assert "-b:a:1" in cmd and "192k" in cmd[cmd.index("-b:a:1") + 1]
    assert "-map" in cmd and "0:a:0" in cmd
    assert "-map" in cmd and "1:a:0" in cmd


def test_build_mux_command_encodes_tts_when_no_original_audio(tmp_path):
    media = tmp_path / "film.mkv"
    tts = tmp_path / "tts.wav"
    out = tmp_path / "out.mkv"
    media.write_bytes(b"m")
    tts.write_bytes(b"t")

    cmd = build_mux_command(
        media,
        tts,
        out,
        lang_tag="por",
        copy_original_audio=False,
    )

    assert "-c:a:0" in cmd and "aac" in cmd[cmd.index("-c:a:0") + 1]
    assert "-b:a:0" in cmd and "192k" in cmd[cmd.index("-b:a:0") + 1]
    assert "1:a:0" in cmd
    assert "-c:a:1" not in cmd


@pytest.fixture
def dub_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media_dir = tmp_path / "media" / "Movie"
    media_dir.mkdir(parents=True)
    video = media_dir / "Film.mkv"
    video.write_bytes(b"fake-video")
    target_srt = media_dir / "Film.pt.srt"
    target_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nOlá\n\n",
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
    yield db, tmp_path, video, target_srt
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
async def test_gate_false_without_target_srt(dub_env):
    db, tmp_path, video, target_srt = dub_env
    target_srt.unlink()
    media = _media(db, video)
    gate = await JobService(db).dub_gate_for_media(media, target_language="pt-PT")
    assert gate.can_dub is False
    assert gate.reason == "Localize subtitles first"


@pytest.mark.asyncio
async def test_gate_false_when_dub_exists(dub_env):
    db, tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    dub_path = build_dub_preview_path(video, "pt-PT")
    dub_path.write_bytes(b"existing-dub")
    gate = await JobService(db).dub_gate_for_media(media, target_language="pt-PT")
    assert gate.can_dub is False
    assert gate.reason == "Dub already exists"


@pytest.mark.asyncio
async def test_gate_true_with_target_srt(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    gate = await JobService(db).dub_gate_for_media(media, target_language="pt-PT")
    assert gate.can_dub is True


@pytest.mark.asyncio
async def test_start_manual_dub_replace_existing_deletes_old_dub(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    dub_path = build_dub_preview_path(video, "pt-PT")
    dub_path.write_bytes(b"old-dub")

    created = await JobService(db).start_manual_dub(
        media,
        target_language="pt-PT",
        replace_existing=True,
    )
    row = db.get(JobRow, created.id)
    assert row is not None
    assert row.job_kind == "dub"
    assert not dub_path.exists()


@pytest.mark.asyncio
async def test_start_manual_dub_creates_job(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    created = await JobService(db).start_manual_dub(media, target_language="pt-PT")
    row = db.get(JobRow, created.id)
    assert row is not None
    assert row.job_kind == "dub"
    assert row.source_subtitle_path.endswith(".pt.srt")
    assert row.target_subtitle_path.endswith(".pt.dub.mkv")
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_process_dub_job_writes_mkv_and_log(dub_env, monkeypatch):
    db, tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    original_bytes = video.read_bytes()

    async def fake_dub(**kwargs):
        out = Path(kwargs["output_path"])
        out.write_bytes(b"fake-dub-mkv")
        kwargs["event_log"].record(
            event="started",
            media_path=kwargs["media_path"],
            target_language=kwargs["target_language"],
        )
        kwargs["event_log"].record(event="source_srt", path=kwargs["source_srt_path"], cue_count=1)
        kwargs["event_log"].record(event="completed", output_path=str(out))

    monkeypatch.setattr("app.jobs.service.dub_media_from_srt_to_mkv", fake_dub)

    created = await JobService(db).start_manual_dub(media, target_language="pt-PT")
    row = db.get(JobRow, created.id)
    row.status = "processing"
    db.add(row)
    db.commit()

    await JobService(db).process_job(created.id)

    done = db.get(JobRow, created.id)
    assert done.status == "completed"
    assert Path(done.target_subtitle_path).is_file()
    assert video.read_bytes() == original_bytes

    log_path = job_event_log_path(get_app_config().config_dir, created.id)
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert '"event": "started"' in log_text
    assert '"event": "completed"' in log_text

    log_out = JobService(db).get_job_log(created.id)
    assert log_out is not None
    assert any(entry.get("event") == "completed" for entry in log_out.entries)


@pytest.mark.asyncio
async def test_planner_completes_when_dub_on_disk(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    svc = LocalizationTaskService(db)
    task, _ = svc.create_manual_task(media_item=media, target_language="pt-PT", capability="audio")

    dub_path = build_dub_preview_path(video, "pt-PT")
    dub_path.write_bytes(b"ready-dub")

    planned = await TaskPlanner(db).plan(task.id)
    assert planned is not None
    assert planned.status == "completed"
    cps = read_checkpoints(svc.get(task.id).metadata_json)
    assert cps["write"] == "done"
    assert cps["verify"] == "done"


def test_worker_includes_dub_kind():
    assert "dub" in JOB_KINDS


def test_claim_dub_independent_of_translate(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    db.add(
        JobRow(
            job_kind="translate",
            media_type="movie",
            media_path=str(video),
            source_subtitle_path=str(video.parent / "Film.en.srt"),
            target_subtitle_path=str(video.parent / "Film.pt.srt"),
            model="test",
            status="pending",
        )
    )
    db.add(
        JobRow(
            job_kind="dub",
            media_type="movie",
            media_path=str(video),
            source_subtitle_path=str(video.parent / "Film.pt.srt"),
            target_subtitle_path=str(build_dub_preview_path(video, "pt-PT")),
            model="pt_PT-tugão-medium",
            status="pending",
        )
    )
    db.commit()
    claimed = JobService(db).claim_next_job(job_kind="dub")
    assert claimed is not None
    assert claimed.job_kind == "dub"
    leftover = db.scalar(select(JobRow).where(JobRow.job_kind == "translate", JobRow.status == "pending"))
    assert leftover is not None
