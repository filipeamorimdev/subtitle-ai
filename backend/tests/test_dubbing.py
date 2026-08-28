"""TTS dubbing tests (mocked Chatterbox/ffmpeg)."""

from __future__ import annotations

import array
import sys
import types
import wave
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
    CUE_SAMPLE_RATE,
    ChatterboxVoiceProfile,
    build_mux_command,
    clean_text_for_tts,
    resolve_voice_model_for_language,
    resolve_voice_profile,
    tts_output_ignores_text,
    write_tts_timeline_wav,
    _write_chatterbox_wav,
)
from app.localization.dubbing.mixer import DUB_OUTPUT_SAMPLE_RATE, build_background_mix_command
from app.localization.dubbing.options import cue_key, normalize_speaker_voice_overrides


def test_chatterbox_profiles_are_language_aware_and_reject_arbitrary_models():
    model = resolve_voice_model_for_language("pt-PT")
    profile = resolve_voice_profile(model, "pt-PT")
    assert profile.language_id == "pt"
    assert profile.id.endswith(":natural")
    with pytest.raises(Exception, match="Unknown Chatterbox"):
        resolve_voice_profile("some-random-model", "pt-PT")


def test_clean_text_for_tts_strips_music_speaker_and_sfx():
    assert "família" in clean_text_for_tts(
        "♪ Bem, viemos de lugares diferentes Mas somos todos família ♪"
    )
    assert "♪" not in clean_text_for_tts("♪ No Dino Ranch ♪")
    assert clean_text_for_tts("Miguel: Para! Para!") == "Para! Para!"
    assert clean_text_for_tts("Bo (voz off): Aquele tricerátopo grande é o Angus.") == (
        "Aquele tricerátopo grande é o Angus."
    )
    assert clean_text_for_tts("(chilrear)") == ""
    assert clean_text_for_tts("(O Jon a ler)") == ""
    assert "equitação" in clean_text_for_tts(
        "é uma óptima maneira de aperfeiçoarmos a nossa equitação."
    )


def test_cue_voice_override_keys_are_normalized_for_ai_cast_assignments():
    overrides = normalize_speaker_voice_overrides(
        {"Cue:42": " chatterbox-multilingual-v3:pt-PT:expressive "}
    )
    assert cue_key(42) == "cue:42"
    assert overrides[cue_key(42)] == "chatterbox-multilingual-v3:pt-PT:expressive"


def test_tts_output_ignores_text_detects_fixed_length_clips():
    # Job 763 pattern: 3–64 chars, every clip ~1.5s.
    samples = [
        (61, 1.544),
        (47, 1.544),
        (3, 1.509),
        (64, 1.521),
        (8, 1.474),
        (45, 1.498),
        (10, 1.486),
        (38, 1.498),
    ]
    assert tts_output_ignores_text(samples) is True
    varying = [
        (8, 0.6),
        (20, 1.4),
        (40, 2.6),
        (12, 0.9),
        (55, 3.4),
        (30, 2.0),
        (18, 1.2),
        (48, 3.0),
    ]
    assert tts_output_ignores_text(varying) is False


class _FakeTensor:
    def detach(self):
        return self

    def cpu(self):
        return self


class _FakeChatterboxModel:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.sr = 24_000

    def generate(self, text: str, **kwargs):
        self.texts.append(text)
        return _FakeTensor()


def test_write_chatterbox_wav_sends_cue_text_and_profile(tmp_path, monkeypatch):
    voice = _FakeChatterboxModel()
    out = tmp_path / "cue.wav"
    captured: dict[str, object] = {}

    def save(path, audio, sample_rate):
        captured.update({"path": path, "audio": audio, "sample_rate": sample_rate})
        Path(path).write_bytes(b"wav" * 24)

    monkeypatch.setitem(sys.modules, "torchaudio", types.SimpleNamespace(save=save))
    _write_chatterbox_wav(
        voice,
        ChatterboxVoiceProfile(
            id="chatterbox-multilingual-v3:pt-PT:expressive",
            label="Expressive",
            language_id="pt",
            exaggeration=0.72,
            cfg_weight=0.35,
            temperature=0.8,
        ),
        "Para! Para!",
        out,
    )
    assert voice.texts == ["Para! Para!"]
    assert captured["sample_rate"] == 24_000
    assert out.stat().st_size > 64


def _write_pcm_wav(path: Path, samples: array.array, *, sample_rate: int = CUE_SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def _read_pcm_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as handle:
        samples = array.array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
        return samples


def test_timeline_places_distinct_clips_without_ffmpeg(tmp_path):
    clip_a = tmp_path / "a.wav"
    clip_b = tmp_path / "b.wav"
    out = tmp_path / "mix.wav"
    _write_pcm_wav(clip_a, array.array("h", [1200] * 800))
    _write_pcm_wav(clip_b, array.array("h", [2400] * 800))

    write_tts_timeline_wav(
        [(clip_a, 0), (clip_b, 1000)],
        out,
        media_duration_s=2.0,
    )

    mixed = _read_pcm_wav(out)
    first = mixed[100]
    quiet = mixed[CUE_SAMPLE_RATE // 2]
    second = mixed[CUE_SAMPLE_RATE + 100]

    assert len(mixed) == CUE_SAMPLE_RATE * 2
    assert quiet == 0
    assert first != 0
    assert second != 0
    assert max(abs(sample) for sample in mixed) < 32767


def test_timeline_places_clip_beyond_adelay_ms_limit(tmp_path):
    clip_a = tmp_path / "a.wav"
    clip_b = tmp_path / "b.wav"
    out = tmp_path / "mix.wav"
    _write_pcm_wav(clip_a, array.array("h", [1500] * 400))
    _write_pcm_wav(clip_b, array.array("h", [3000] * 400))

    late_ms = 70_000
    write_tts_timeline_wav(
        [(clip_a, 0), (clip_b, late_ms)],
        out,
        media_duration_s=71.0,
    )

    mixed = _read_pcm_wav(out)
    late_index = int(round(late_ms * CUE_SAMPLE_RATE / 1000.0))

    assert mixed[100] != 0
    assert mixed[CUE_SAMPLE_RATE * 5] == 0
    assert mixed[late_index + 50] != 0
    assert max(abs(sample) for sample in mixed[CUE_SAMPLE_RATE : CUE_SAMPLE_RATE * 2]) == 0


def test_build_mux_command_makes_portuguese_dub_default_and_keeps_original_audio(tmp_path):
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

    assert "-c:a:0" in cmd and "aac" in cmd[cmd.index("-c:a:0") + 1]
    assert "-b:a:0" in cmd and "192k" in cmd[cmd.index("-b:a:0") + 1]
    assert "-c:a:1" in cmd and "copy" in cmd[cmd.index("-c:a:1") + 1]
    assert cmd[cmd.index("-disposition:a:0") + 1] == "default"
    assert cmd[cmd.index("-disposition:a:1") + 1] == "0"
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


def test_background_mix_command_outputs_stereo_48khz(tmp_path):
    cmd = build_background_mix_command(
        tmp_path / "background.wav",
        tmp_path / "dialogue.wav",
        tmp_path / "mix.wav",
    )

    assert "sidechaincompress" in cmd[cmd.index("-filter_complex") + 1]
    assert "amix" in cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-ar") + 1] == str(DUB_OUTPUT_SAMPLE_RATE)


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
    assert row.dub_mix_mode == "background_preserved"
    assert row.dub_speaker_voices == {}


@pytest.mark.asyncio
async def test_start_manual_dub_persists_requested_mix_and_speaker_voices(dub_env):
    db, _tmp_path, video, _target_srt = dub_env
    media = _media(db, video)

    created = await JobService(db).start_manual_dub(
        media,
        target_language="pt-PT",
        mix_mode="voiceover_preview",
        speaker_voice_overrides={"Ryder": "chatterbox-multilingual-v3:pt-PT:expressive"},
    )

    row = db.get(JobRow, created.id)
    assert row is not None
    assert row.dub_mix_mode == "voiceover_preview"
    assert row.dub_speaker_voices == {"ryder": "chatterbox-multilingual-v3:pt-PT:expressive"}


@pytest.mark.asyncio
async def test_process_dub_job_writes_mkv_and_log(dub_env, monkeypatch):
    db, tmp_path, video, _target_srt = dub_env
    media = _media(db, video)
    original_bytes = video.read_bytes()

    captured: dict[str, object] = {}

    async def fake_dub(**kwargs):
        captured.update(kwargs)
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
    assert captured["mix_mode"] == "background_preserved"
    assert captured["speaker_voice_overrides"] == {}


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
            model="chatterbox-multilingual-v3:pt-PT:natural",
            status="pending",
        )
    )
    db.commit()
    claimed = JobService(db).claim_next_job(job_kind="dub")
    assert claimed is not None
    assert claimed.job_kind == "dub"
    leftover = db.scalar(select(JobRow).where(JobRow.job_kind == "translate", JobRow.status == "pending"))
    assert leftover is not None
