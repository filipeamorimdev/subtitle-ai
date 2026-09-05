"""TTS dubbing tests (mocked Chatterbox/ffmpeg)."""

from __future__ import annotations

import array
import inspect
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
from app.jobs.event_log import JobEventLog, job_event_log_path
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
from app.localization.dubbing.cache import DubCueCache, dub_cache_key
from app.localization.dubbing.options import cue_key, normalize_speaker_voice_overrides
from app.localization.dubbing.pipeline import DubError, DubbingPipeline, shape_clip_filters
from app.localization.dubbing.providers import chatterbox as chatterbox_provider
from app.localization.dubbing.providers.chatterbox import ChatterboxTTSProvider, TTSError
from app.localization.dubbing.timing import TimingEngine
from app.localization.dubbing.voice_library.qa import peak_limit_wav, validate_generated_cue


def test_installed_chatterbox_supports_multilingual_v3_loader():
    """Keep the dependency pin aligned with the runtime's V3 loader call."""
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    assert "t3_model" in inspect.signature(ChatterboxMultilingualTTS.from_pretrained).parameters


def test_load_chatterbox_model_requests_the_v3_checkpoint(monkeypatch):
    calls: list[dict[str, str]] = []

    class FakeMultilingualTTS:
        @classmethod
        def from_pretrained(cls, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(conds=object(), device=kwargs["device"])

    chatterbox_package = types.ModuleType("chatterbox")
    chatterbox_package.__path__ = []
    multilingual_module = types.ModuleType("chatterbox.mtl_tts")
    multilingual_module.ChatterboxMultilingualTTS = FakeMultilingualTTS
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox_package)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", multilingual_module)
    monkeypatch.setattr(chatterbox_provider, "_MODEL_BY_DEVICE", {})

    loaded = chatterbox_provider.load_chatterbox_model(device="cpu")

    assert loaded.device == "cpu"
    assert calls == [{"device": "cpu", "t3_model": "v3"}]


def test_load_chatterbox_model_explains_an_outdated_package(monkeypatch):
    chatterbox_package = types.ModuleType("chatterbox")
    chatterbox_package.__path__ = []
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox_package)
    monkeypatch.delitem(sys.modules, "chatterbox.mtl_tts", raising=False)
    monkeypatch.setattr(chatterbox_provider, "_MODEL_BY_DEVICE", {})

    with pytest.raises(TTSError, match="does not support Multilingual V3"):
        chatterbox_provider.load_chatterbox_model(device="cpu")


def _fake_multilingual_tts(local_calls: list, pretrained_calls: list | None = None):
    class FakeMultilingualTTS:
        @classmethod
        def from_local(cls, ckpt_dir, device, t3_model=None):
            local_calls.append({"ckpt_dir": Path(ckpt_dir), "device": device, "t3_model": t3_model})
            return types.SimpleNamespace(conds=object(), device=device)

        @classmethod
        def from_pretrained(cls, **kwargs):
            if pretrained_calls is not None:
                pretrained_calls.append(kwargs)
            raise AssertionError("pt-PT should use the assembled local overlay")

    chatterbox_package = types.ModuleType("chatterbox")
    chatterbox_package.__path__ = []
    multilingual_module = types.ModuleType("chatterbox.mtl_tts")
    multilingual_module.ChatterboxMultilingualTTS = FakeMultilingualTTS
    return chatterbox_package, multilingual_module


def test_ensure_pt_pt_checkpoint_fetches_shared_companions(tmp_path, monkeypatch):
    downloads: list[tuple[str, tuple[str, ...]]] = []

    def fake_snapshot_download(*, repo_id, local_dir, allow_patterns, **_kwargs):
        downloads.append((repo_id, tuple(allow_patterns)))
        root = Path(local_dir)
        created = {
            "t3_pt_pt.safetensors": "t3",
            "grapheme_mtl_merged_expanded_v1.json": "{}",
            "ve.pt": "ve",
            "conds.pt": "conds",
            "s3gen.pt": "s3",
        }
        for name in allow_patterns:
            root.joinpath(name).write_text(created[name])

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    get_app_config.cache_clear()
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        fake_snapshot_download,
    )

    cache_root = chatterbox_provider._ensure_pt_pt_checkpoint()

    assert cache_root == tmp_path / "config" / "huggingface" / "chatterbox-pt-pt"
    assert (cache_root / "ve.pt").read_text() == "ve"
    assert (cache_root / "conds.pt").read_text() == "conds"
    assert (cache_root / "s3gen.pt").read_text() == "s3"
    assert downloads == [
        ("ResembleAI/Chatterbox-Multilingual-pt-pt", chatterbox_provider._PT_PT_OVERLAY_FILES),
        ("ResembleAI/chatterbox", chatterbox_provider._PT_PT_COMPANION_FILES),
    ]


def test_ensure_pt_pt_checkpoint_replaces_s3gen_v3_alias(tmp_path, monkeypatch):
    cache_root = tmp_path / "config" / "huggingface" / "chatterbox-pt-pt"
    cache_root.mkdir(parents=True)
    (cache_root / "t3_pt_pt.safetensors").write_text("t3")
    (cache_root / "grapheme_mtl_merged_expanded_v1.json").write_text("{}")
    (cache_root / "ve.pt").write_text("ve")
    (cache_root / "conds.pt").write_text("conds")
    (cache_root / "s3gen_v3.pt").write_text("s3-overlay")
    (cache_root / "s3gen.pt").symlink_to("s3gen_v3.pt")

    def fake_snapshot_download(*, repo_id, local_dir, allow_patterns, **_kwargs):
        root = Path(local_dir)
        if repo_id == "ResembleAI/chatterbox" and allow_patterns == ["s3gen.pt"]:
            root.joinpath("s3gen.pt").write_text("official-s3gen")
            return
        raise AssertionError(f"unexpected download: {repo_id} {allow_patterns}")

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    get_app_config.cache_clear()
    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

    resolved = chatterbox_provider._ensure_pt_pt_checkpoint()

    assert resolved == cache_root
    assert (cache_root / "s3gen.pt").read_text() == "official-s3gen"
    assert (cache_root / "s3gen_v3.pt").read_text() == "s3-overlay"


def test_ensure_pt_pt_checkpoint_skips_download_when_complete(tmp_path, monkeypatch):
    cache_root = tmp_path / "config" / "huggingface" / "chatterbox-pt-pt"
    cache_root.mkdir(parents=True)
    for name in chatterbox_provider._PT_PT_REQUIRED_FILES:
        (cache_root / name).write_text(name)

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    get_app_config.cache_clear()
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("complete cache should not download")),
    )

    assert chatterbox_provider._ensure_pt_pt_checkpoint() == cache_root


def test_merge_optional_s3gen_buffers_restores_tokenizer_keys():
    class FakeTokenizer:
        ignore_state_dict_missing = ("_mel_filters", "window")

    class FakeS3Gen:
        ignore_state_dict_missing = ("tokenizer._mel_filters", "tokenizer.window")

        def __init__(self):
            self.tokenizer = FakeTokenizer()
            self._state = {
                "weight": "live-weight",
                "tokenizer._mel_filters": "live-filters",
                "tokenizer.window": "live-window",
            }

        def state_dict(self):
            return dict(self._state)

    merged = chatterbox_provider._merge_optional_s3gen_buffers(
        FakeS3Gen(),
        {"weight": "ckpt-weight"},
    )

    assert merged == {
        "weight": "ckpt-weight",
        "tokenizer._mel_filters": "live-filters",
        "tokenizer.window": "live-window",
    }


def test_install_s3gen_checkpoint_compat_patches_once(monkeypatch):
    class FakeS3Gen:
        def load_state_dict(self, state_dict, *args, **kwargs):
            raise AssertionError("original loader should be replaced")

    chatterbox_package = types.ModuleType("chatterbox")
    models_module = types.ModuleType("chatterbox.models")
    s3gen_module = types.ModuleType("chatterbox.models.s3gen")
    s3gen_module.S3Gen = FakeS3Gen
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox_package)
    monkeypatch.setitem(sys.modules, "chatterbox.models", models_module)
    monkeypatch.setitem(sys.modules, "chatterbox.models.s3gen", s3gen_module)

    chatterbox_provider._install_s3gen_checkpoint_compat()
    patched = FakeS3Gen.load_state_dict
    chatterbox_provider._install_s3gen_checkpoint_compat()

    assert FakeS3Gen.load_state_dict is patched
    assert patched._subtitle_ai_compat is True


def test_load_chatterbox_model_installs_s3gen_checkpoint_compat(monkeypatch):
    calls: list[int] = []

    class FakeMultilingualTTS:
        @classmethod
        def from_pretrained(cls, **kwargs):
            return types.SimpleNamespace(conds=object(), device=kwargs["device"])

    chatterbox_package = types.ModuleType("chatterbox")
    chatterbox_package.__path__ = []
    multilingual_module = types.ModuleType("chatterbox.mtl_tts")
    multilingual_module.ChatterboxMultilingualTTS = FakeMultilingualTTS
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox_package)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", multilingual_module)
    monkeypatch.setattr(chatterbox_provider, "_MODEL_BY_DEVICE", {})
    monkeypatch.setattr(
        chatterbox_provider,
        "_install_s3gen_checkpoint_compat",
        lambda: calls.append(1),
    )

    chatterbox_provider.load_chatterbox_model(device="cpu")

    assert calls == [1]


def test_load_chatterbox_model_uses_assembled_pt_pt_overlay(tmp_path, monkeypatch):
    local_calls: list[dict[str, object]] = []
    chatterbox_package, multilingual_module = _fake_multilingual_tts(local_calls)
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox_package)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", multilingual_module)
    monkeypatch.setattr(chatterbox_provider, "_MODEL_BY_DEVICE", {})
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    get_app_config.cache_clear()

    def fake_snapshot_download(*, repo_id, local_dir, allow_patterns, **_kwargs):
        root = Path(local_dir)
        for name in allow_patterns:
            root.joinpath(name).write_text(name)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

    loaded = chatterbox_provider.load_chatterbox_model(device="cpu", target_language="pt-PT")

    assert loaded.device == "cpu"
    assert local_calls == [
        {
            "ckpt_dir": tmp_path / "config" / "huggingface" / "chatterbox-pt-pt",
            "device": "cpu",
            "t3_model": "t3_pt_pt.safetensors",
        }
    ]
    assert (local_calls[0]["ckpt_dir"] / "ve.pt").is_file()
    assert (local_calls[0]["ckpt_dir"] / "s3gen.pt").is_file()


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


def test_shape_clip_filters_limit_peaks_after_resample():
    assert shape_clip_filters(1.0) == [
        f"aresample={CUE_SAMPLE_RATE}",
        "alimiter=limit=0.95:level=disabled",
    ]
    assert shape_clip_filters(1.12)[0].startswith("atempo=")
    assert shape_clip_filters(1.12)[-1] == "alimiter=limit=0.95:level=disabled"


def test_validate_generated_cue_allows_isolated_full_scale_sample(tmp_path):
    samples = array.array("h", [1200] * 8_000)
    samples[400] = 32767
    wav = tmp_path / "cue.wav"
    _write_pcm_wav(wav, samples)
    report = validate_generated_cue(wav, expected_text="A Min trata de animar")
    assert report.ok is True
    assert report.reasons == []


def test_validate_generated_cue_rejects_sustained_clipping(tmp_path):
    wav = tmp_path / "hot.wav"
    _write_pcm_wav(wav, array.array("h", [32767] * 8_000))
    report = validate_generated_cue(wav, expected_text="A Min trata de animar")
    assert report.ok is False
    assert report.reasons == ["clipped"]


def test_peak_limit_wav_repairs_full_scale_cue(tmp_path):
    wav = tmp_path / "hot.wav"
    _write_pcm_wav(wav, array.array("h", [32767] * 8_000))
    assert peak_limit_wav(wav) is True
    repaired = validate_generated_cue(wav, expected_text="A Min trata de animar")
    assert repaired.ok is True
    assert max(abs(value) for value in _read_pcm_wav(wav)) <= int(0.95 * 32768)


def test_limit_tts_peak_scales_hot_float_audio():
    class HotTensor:
        def __init__(self, peak: float) -> None:
            self.peak = peak

        def abs(self):
            return self

        def max(self):
            return self.peak

        def __mul__(self, scale: float):
            return HotTensor(self.peak * scale)

    limited = chatterbox_provider._limit_tts_peak(HotTensor(1.4))
    assert abs(limited.peak - 0.95) < 1e-6
    untouched = chatterbox_provider._limit_tts_peak(HotTensor(0.4))
    assert untouched.peak == 0.4


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


def test_dub_cue_cache_round_trip_and_input_keying(tmp_path):
    timing = TimingEngine()
    key = dub_cache_key(
        source_srt="one subtitle",
        target_language="pt-PT",
        voice_model="chatterbox-multilingual-v3:pt-PT:natural",
        speaker_voice_overrides={},
        voice_bindings={},
        timing=timing,
    )
    changed_key = dub_cache_key(
        source_srt="changed subtitle",
        target_language="pt-PT",
        voice_model="chatterbox-multilingual-v3:pt-PT:natural",
        speaker_voice_overrides={},
        voice_bindings={},
        timing=timing,
    )
    binding_key = dub_cache_key(
        source_srt="one subtitle",
        target_language="pt-PT",
        voice_model="chatterbox-multilingual-v3:pt-PT:natural",
        speaker_voice_overrides={},
        voice_bindings={"cue:1": "abc|model|0.35"},
        timing=timing,
    )
    assert key != changed_key
    assert key != binding_key

    shaped = tmp_path / "shaped.wav"
    _write_pcm_wav(shaped, array.array("h", [1200] * 800))
    decision = timing.decide(actual=1.1, available=1.0)
    cache = DubCueCache(tmp_path / "cache", key)
    cached_path = cache.store(3, shaped, actual=1.1, decision=decision)

    restored = cache.load(3)
    assert restored is not None
    assert restored.path == cached_path
    assert restored.actual == 1.1
    assert restored.decision == decision

    cache.clear()
    assert cache.load(3) is None


@pytest.mark.asyncio
async def test_dub_retry_uses_checkpoint_without_loading_model(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"media")
    source_srt = tmp_path / "episode.pt.srt"
    source_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nPrimeira fala\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nSegunda fala\n\n",
        encoding="utf-8",
    )
    synth_calls: list[str] = []
    model_loads = 0

    def fake_load_model(**_kwargs):
        nonlocal model_loads
        model_loads += 1
        return types.SimpleNamespace(device="cpu")

    async def fake_synthesize(self, text, voice, language, *, output_path, is_cancelled=None):
        from app.localization.artifacts import AudioArtifact

        synth_calls.append(text)
        _write_pcm_wav(Path(output_path), array.array("h", [1200] * 8_000))
        return AudioArtifact(
            path=str(output_path),
            duration=0.5,
            sample_rate=CUE_SAMPLE_RATE,
            channels=1,
            provider="fake",
        )

    async def fake_shape(input_wav, output_wav, **kwargs):
        Path(output_wav).write_bytes(Path(input_wav).read_bytes())

    monkeypatch.setattr("app.localization.dubbing.pipeline.load_chatterbox_model", fake_load_model)
    monkeypatch.setattr(ChatterboxTTSProvider, "synthesize", fake_synthesize)
    monkeypatch.setattr("app.localization.dubbing.pipeline.shape_clip", fake_shape)

    cache_dir = tmp_path / "cache"
    for attempt in (1, 2):
        cancelled = False

        async def stop_after_first(done, total):
            nonlocal cancelled
            if done == 1:
                cancelled = True

        result = await DubbingPipeline().run(
            media_path=media,
            source_srt_path=source_srt,
            target_language="pt-PT",
            output_path=tmp_path / "episode.pt.dub.mkv",
            event_log=JobEventLog(tmp_path / f"job-{attempt}.jsonl", job_id=attempt),
            is_cancelled=lambda: cancelled,
            on_progress=stop_after_first,
            cache_dir=cache_dir,
        )
        assert result is None

    assert synth_calls == ["Primeira fala"]
    assert model_loads == 1
    assert '"event": "speech_cached"' in (tmp_path / "job-2.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_slow_cue_is_checkpointed_before_requesting_clean_retry(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"media")
    source_srt = tmp_path / "episode.pt.srt"
    source_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nFala demorada\n\n",
        encoding="utf-8",
    )

    async def fake_synthesize(self, text, voice, language, *, output_path, is_cancelled=None):
        from app.localization.artifacts import AudioArtifact

        _write_pcm_wav(Path(output_path), array.array("h", [1200] * 8_000))
        return AudioArtifact(
            path=str(output_path),
            duration=0.5,
            sample_rate=CUE_SAMPLE_RATE,
            channels=1,
            provider="fake",
        )

    async def fake_shape(input_wav, output_wav, **kwargs):
        Path(output_wav).write_bytes(Path(input_wav).read_bytes())

    ticks = iter((0.0, 601.0))
    monkeypatch.setattr(
        "app.localization.dubbing.pipeline.load_chatterbox_model",
        lambda **_kwargs: types.SimpleNamespace(device="cpu"),
    )
    monkeypatch.setattr(ChatterboxTTSProvider, "synthesize", fake_synthesize)
    monkeypatch.setattr("app.localization.dubbing.pipeline.shape_clip", fake_shape)
    monkeypatch.setattr("app.localization.dubbing.pipeline._monotonic", lambda: next(ticks))

    event_path = tmp_path / "job.jsonl"
    cache_dir = tmp_path / "cache"
    with pytest.raises(DubError, match="completed cue was checkpointed"):
        await DubbingPipeline().run(
            media_path=media,
            source_srt_path=source_srt,
            target_language="pt-PT",
            output_path=tmp_path / "episode.pt.dub.mkv",
            event_log=JobEventLog(event_path, job_id=1),
            is_cancelled=lambda: False,
            cache_dir=cache_dir,
            max_cue_seconds=600,
        )

    assert list(cache_dir.rglob("cue-00001.wav"))
    log = event_path.read_text(encoding="utf-8")
    assert '"reason": "slow_cue"' in log
    assert '"seconds": 601.0' in log


@pytest.mark.asyncio
async def test_clipped_cue_is_peak_limited_instead_of_failing(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"media")
    source_srt = tmp_path / "episode.pt.srt"
    source_srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nA Min trata de animar o coração\n\n",
        encoding="utf-8",
    )
    output = tmp_path / "episode.pt.dub.mkv"

    async def fake_synthesize(self, text, voice, language, *, output_path, is_cancelled=None):
        from app.localization.artifacts import AudioArtifact

        _write_pcm_wav(Path(output_path), array.array("h", [32767] * 8_000))
        return AudioArtifact(
            path=str(output_path),
            duration=0.5,
            sample_rate=CUE_SAMPLE_RATE,
            channels=1,
            provider="fake",
        )

    async def fake_shape(input_wav, output_wav, **kwargs):
        Path(output_wav).write_bytes(Path(input_wav).read_bytes())

    async def fake_finalize(path, **_kwargs):
        return Path(path)

    async def fake_probe_duration(_path):
        return 3.0

    async def fake_has_audio(_path):
        return False

    async def fake_probe_artifact(path):
        from app.localization.artifacts import MediaArtifact

        Path(path).write_bytes(b"mkv")
        return MediaArtifact(path=str(path), duration=3.0, audio_streams=1, verified=True)

    async def fake_run(*_args, **kwargs):
        for item in kwargs.get("output_paths") or []:
            Path(item).write_bytes(b"out")
        return types.SimpleNamespace()

    monkeypatch.setattr(
        "app.localization.dubbing.pipeline.load_chatterbox_model",
        lambda **_kwargs: types.SimpleNamespace(device="cpu"),
    )
    monkeypatch.setattr(ChatterboxTTSProvider, "synthesize", fake_synthesize)
    monkeypatch.setattr("app.localization.dubbing.pipeline.shape_clip", fake_shape)
    monkeypatch.setattr("app.localization.dubbing.pipeline.finalize_dialogue_track", fake_finalize)
    monkeypatch.setattr("app.localization.dubbing.pipeline.probe_duration_seconds", fake_probe_duration)
    monkeypatch.setattr("app.localization.dubbing.pipeline.probe_has_audio_stream", fake_has_audio)
    monkeypatch.setattr("app.localization.dubbing.pipeline.probe_media_artifact", fake_probe_artifact)
    monkeypatch.setattr("app.localization.dubbing.pipeline.run_process_checked", fake_run)

    event_path = tmp_path / "job.jsonl"
    result = await DubbingPipeline().run(
        media_path=media,
        source_srt_path=source_srt,
        target_language="pt-PT",
        output_path=output,
        event_log=JobEventLog(event_path, job_id=848),
        is_cancelled=lambda: False,
    )

    assert result is not None
    assert result.verified is True
    assert '"event": "cue_peak_limited"' in event_path.read_text(encoding="utf-8")


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

    def save(path, audio, sample_rate, **kwargs):
        captured.update({"path": path, "audio": audio, "sample_rate": sample_rate, **kwargs})
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
    assert captured["format"] == "wav"
    assert captured["encoding"] == "PCM_S"
    assert captured["bits_per_sample"] == 16
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
