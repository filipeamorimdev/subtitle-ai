"""Isolated audio separation tests. Real Demucs is not required."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_app_config
from app.localization.artifacts import AudioArtifact
from app.localization.audio.debug import DebugTrace, open_debug_trace
from app.localization.audio.models import (
    CODE_CANCELLED,
    CODE_INVALID_OUTPUT,
    CODE_MISSING_BACKGROUND,
    CODE_MISSING_DIALOGUE,
    CODE_SEPARATION_FAILED,
    CODE_VERIFICATION_FAILED,
    ROLE_BACKGROUND,
    ROLE_DIALOGUE,
    AudioSeparationError,
    SeparationResult,
    duration_tolerance_s,
)
from app.localization.audio.separation import AudioSeparationService, probe_wav
from tests.fixtures.media import build_multitrack_mkv, ffmpeg_available, write_sine_wav


class FakeProvider:
    name = "fake"
    model = "fake-stems"

    def __init__(
        self,
        *,
        fail: bool = False,
        write_dialogue: bool = True,
        write_background: bool = True,
        empty_dialogue: bool = False,
        invalid_dialogue: bool = False,
        empty_background: bool = False,
        invalid_background: bool = False,
        dialogue_duration: float | None = None,
        background_duration: float | None = None,
        cancel: bool = False,
    ) -> None:
        self.fail = fail
        self.write_dialogue = write_dialogue
        self.write_background = write_background
        self.empty_dialogue = empty_dialogue
        self.invalid_dialogue = invalid_dialogue
        self.empty_background = empty_background
        self.invalid_background = invalid_background
        self.dialogue_duration = dialogue_duration
        self.background_duration = background_duration
        self.cancel = cancel
        self.last_input: Path | None = None

    async def separate(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        debug=None,
        is_cancelled=None,
    ) -> SeparationResult:
        self.last_input = Path(input_path)
        if debug:
            debug.event("provider", "fake provider started", input_path=str(input_path))
        if self.cancel or (is_cancelled and is_cancelled()):
            if debug:
                debug.event("cleanup", "cancellation detected; discarding partial stems")
            raise AudioSeparationError(
                "Audio separation cancelled.",
                code=CODE_CANCELLED,
                stage="provider",
            )
        if self.fail:
            if debug:
                debug.event("provider", "simulated provider failure")
            raise AudioSeparationError(
                "simulated provider failure",
                code=CODE_SEPARATION_FAILED,
                stage="provider",
            )
        dest = Path(output_dir)
        dest.mkdir(parents=True, exist_ok=True)
        source = probe_wav(self.last_input)
        source_duration = source.duration if source and source.duration else 2.0
        dialogue = dest / "dialogue.wav"
        background = dest / "background.wav"
        if self.write_dialogue:
            _write_stem(
                dialogue,
                duration_s=self.dialogue_duration or source_duration,
                frequency=440,
                empty=self.empty_dialogue,
                invalid=self.invalid_dialogue,
            )
        if self.write_background:
            _write_stem(
                background,
                duration_s=self.background_duration or source_duration,
                frequency=220,
                empty=self.empty_background,
                invalid=self.invalid_background,
            )
        if debug:
            debug.event(
                "provider",
                "fake stems written",
                dialogue=str(dialogue),
                background=str(background),
            )
        return SeparationResult(
            dialogue=AudioArtifact(
                path=str(dialogue if self.write_dialogue else dest / "missing-dialogue.wav"),
                provider=self.name,
                metadata={"role": ROLE_DIALOGUE},
            ),
            background=AudioArtifact(
                path=str(background if self.write_background else dest / "missing-background.wav"),
                provider=self.name,
                metadata={"role": ROLE_BACKGROUND},
            ),
            provider=self.name,
            model=self.model,
            metadata={},
        )


def _write_stem(path: Path, *, duration_s: float, frequency: float, empty: bool, invalid: bool) -> None:
    if empty:
        path.write_bytes(b"")
        return
    if invalid:
        path.write_text("this is not audio", encoding="utf-8")
        return
    write_sine_wav(path, duration_s=duration_s, frequency=frequency)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(path))
    monkeypatch.delenv("SUBTITLE_AI_DEBUG_TRACE", raising=False)
    get_app_config.cache_clear()
    yield path
    get_app_config.cache_clear()


def _source_wav(tmp_path: Path, *, duration_s: float = 2.0) -> Path:
    return write_sine_wav(tmp_path / "source.wav", duration_s=duration_s, frequency=330)


def _stereo_wav(path: Path, *, duration_s: float = 1.0, sample_rate: int = 44100) -> Path:
    import array
    import math
    import wave

    n_frames = max(1, int(duration_s * sample_rate))
    samples = array.array("h")
    for index in range(n_frames):
        left = int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        right = int(8000 * math.sin(2 * math.pi * 660 * index / sample_rate))
        samples.extend((left, right))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return path


@pytest.mark.asyncio
async def test_successful_separation_returns_named_artifacts(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    provider = FakeProvider()
    result = await AudioSeparationService(provider=provider).separate(
        source,
        output_dir=tmp_path / "out",
        debug=False,
    )
    assert result.dialogue.path and Path(result.dialogue.path).is_file()
    assert result.background.path and Path(result.background.path).is_file()
    assert result.dialogue.metadata["role"] == ROLE_DIALOGUE
    assert result.background.metadata["role"] == ROLE_BACKGROUND
    assert result.provider == "fake"
    assert result.model == "fake-stems"
    assert result.metadata["selected_stream"] is not None
    assert result.metadata["duration"] == pytest.approx(2.0, abs=0.05)
    assert result.dialogue.duration == pytest.approx(2.0, abs=0.05)
    assert result.debug_trace_path is None


@pytest.mark.asyncio
async def test_debug_disabled_creates_no_trace_file(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    result = await AudioSeparationService(provider=FakeProvider()).separate(
        source,
        output_dir=tmp_path / "out",
        task_id="no-trace",
        debug=False,
    )
    debug_root = config_dir / "debug"
    assert result.debug_trace_path is None
    assert not debug_root.exists() or not any(debug_root.rglob("trace.log"))


@pytest.mark.asyncio
async def test_debug_enabled_records_full_success_story(tmp_path, config_dir, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AI_DEBUG_TRACE", "true")
    get_app_config.cache_clear()
    source = _source_wav(tmp_path)
    events: list[str] = []
    result = await AudioSeparationService(provider=FakeProvider()).separate(
        source,
        output_dir=tmp_path / "out",
        task_id="ok-run",
        event_cb=lambda name, _payload: events.append(name),
    )
    assert result.debug_trace_path is not None
    trace = Path(result.debug_trace_path)
    assert trace.is_file()
    text = trace.read_text(encoding="utf-8")
    assert "feature=audio-separation" in text or "feature=audio_separation" in text
    assert "task_id=ok-run" in text
    assert "debug_enabled=True" in text or "debug_enabled=true" in text
    assert "Selected audio stream" in text or "Standalone WAV" in text
    assert "EVENT" in text and "DECISION" in text
    assert "fake provider started" in text
    assert "Output stems verified" in text or "audio_separation_verified" in text
    assert "FINISH" in text and "status=success" in text
    assert "audio_separation_started" in events
    assert "audio_separation_completed" in events
    assert "audio_separation_verified" in events


@pytest.mark.asyncio
async def test_failure_trace_records_stage_and_failed_status(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(fail=True)).separate(
            source,
            output_dir=tmp_path / "out",
            task_id="fail-run",
            debug=True,
        )
    assert raised.value.code == CODE_SEPARATION_FAILED
    trace = config_dir / "debug" / "audio-separation" / "fail-run" / "trace.log"
    text = trace.read_text(encoding="utf-8")
    assert "last_completed_stage" in text or "failure_stage" in text
    assert "simulated provider failure" in text
    assert "status=failed" in text
    assert "status=success" not in text


@pytest.mark.asyncio
async def test_cancellation_trace_and_no_successful_result(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(cancel=True)).separate(
            source,
            output_dir=tmp_path / "out",
            task_id="cancel-run",
            debug=True,
        )
    assert raised.value.code == CODE_CANCELLED
    trace = (config_dir / "debug" / "audio-separation" / "cancel-run" / "trace.log").read_text(
        encoding="utf-8"
    )
    assert "cancelled" in trace.lower()
    assert "cleanup" in trace.lower() or "cancellation detected" in trace
    assert "status=cancelled" in trace
    assert not (tmp_path / "out" / "dialogue.wav").exists()
    assert not (tmp_path / "out" / "background.wav").exists()


@pytest.mark.asyncio
async def test_missing_dialogue_output_fails_explicitly(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(write_dialogue=False)).separate(
            source,
            output_dir=tmp_path / "out",
            debug=False,
        )
    assert raised.value.code == CODE_MISSING_DIALOGUE


@pytest.mark.asyncio
async def test_missing_background_output_fails_explicitly(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(write_background=False)).separate(
            source,
            output_dir=tmp_path / "out",
            debug=False,
        )
    assert raised.value.code == CODE_MISSING_BACKGROUND


@pytest.mark.asyncio
async def test_empty_output_is_rejected(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(empty_dialogue=True)).separate(
            source,
            output_dir=tmp_path / "out",
            debug=False,
        )
    assert raised.value.code == CODE_INVALID_OUTPUT


@pytest.mark.asyncio
async def test_invalid_audio_output_is_rejected(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=FakeProvider(invalid_dialogue=True)).separate(
            source,
            output_dir=tmp_path / "out",
            debug=False,
        )
    assert raised.value.code == CODE_INVALID_OUTPUT


@pytest.mark.asyncio
async def test_duration_validation_accepts_small_differences(tmp_path, config_dir):
    source = _source_wav(tmp_path, duration_s=2.0)
    result = await AudioSeparationService(
        provider=FakeProvider(dialogue_duration=2.08, background_duration=1.97)
    ).separate(source, output_dir=tmp_path / "out", debug=False)
    assert result.dialogue.duration == pytest.approx(2.08, abs=0.05)
    assert result.metadata["verified"] is True


@pytest.mark.asyncio
async def test_duration_validation_rejects_large_mismatch(tmp_path, config_dir):
    source = _source_wav(tmp_path, duration_s=2.0)
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(
            provider=FakeProvider(dialogue_duration=10.0, background_duration=2.0)
        ).separate(source, output_dir=tmp_path / "out", debug=True, task_id="dur-fail")
    assert raised.value.code == CODE_VERIFICATION_FAILED
    text = (config_dir / "debug" / "audio-separation" / "dur-fail" / "trace.log").read_text(
        encoding="utf-8"
    )
    assert "Duration comparison" in text or "duration" in text.lower()
    assert "status=failed" in text


def test_duration_tolerance_bounds():
    assert duration_tolerance_s(2.0) == pytest.approx(0.35)
    assert duration_tolerance_s(200.0) == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_debug_trace_is_written_incrementally(tmp_path):
    path = tmp_path / "trace.log"
    trace = DebugTrace(path, feature="audio_separation", task_id="inc")
    trace.decision("Converted input to WAV", reason="provider requires supported working format")
    text = path.read_text(encoding="utf-8")
    assert "Converted input to WAV" in text
    assert "FINISH" not in text
    trace.finish("success")
    assert "status=success" in path.read_text(encoding="utf-8")


def test_debug_disabled_factory_creates_nothing(config_dir):
    assert open_debug_trace(feature="audio_separation", task_id="x", enabled=False) is None
    assert not (config_dir / "debug").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not installed")
async def test_media_container_is_prepared_to_working_wav(tmp_path, config_dir):
    mkv = build_multitrack_mkv(tmp_path / "episode.mkv", tmp_path / "parts")
    provider = FakeProvider()
    events: list[tuple[str, dict]] = []
    result = await AudioSeparationService(provider=provider).separate(
        mkv,
        output_dir=tmp_path / "out",
        task_id="media-prep",
        debug=True,
        event_cb=lambda name, payload: events.append((name, payload)),
    )
    assert provider.last_input is not None
    assert provider.last_input.suffix.lower() == ".wav"
    assert provider.last_input.name == "source.wav"
    assert result.metadata["input_type"] == "media"
    assert result.metadata["selected_stream"] is not None
    assert result.metadata["selection_reason"]
    names = [name for name, _payload in events]
    assert "audio_separation_input_selected" in names
    assert "audio_separation_input_prepared" in names
    text = Path(result.debug_trace_path).read_text(encoding="utf-8")
    assert "Selected audio stream" in text
    assert "Converted input to WAV" in text or "WAV working format" in text
    assert "COMMAND" in text
    assert "ffmpeg" in text


@pytest.mark.asyncio
async def test_config_debug_trace_defaults_false(config_dir):
    assert get_app_config().debug_trace is False


@pytest.mark.asyncio
async def test_start_cancelled_does_not_call_provider(tmp_path, config_dir):
    source = _source_wav(tmp_path)
    provider = FakeProvider()
    with pytest.raises(AudioSeparationError) as raised:
        await AudioSeparationService(provider=provider).separate(
            source,
            output_dir=tmp_path / "out",
            debug=True,
            task_id="early-cancel",
            is_cancelled=lambda: True,
        )
    assert raised.value.code == CODE_CANCELLED
    assert provider.last_input is None
    text = (config_dir / "debug" / "audio-separation" / "early-cancel" / "trace.log").read_text(
        encoding="utf-8"
    )
    assert "status=cancelled" in text


@pytest.mark.asyncio
async def test_stereo_wav_is_not_forced_to_mono(tmp_path, config_dir):
    source = _stereo_wav(tmp_path / "stereo.wav", duration_s=1.0, sample_rate=44100)
    provider = FakeProvider()
    result = await AudioSeparationService(provider=provider).separate(
        source,
        output_dir=tmp_path / "out",
        debug=False,
    )
    assert provider.last_input is not None
    assert provider.last_input.name == "source.wav"
    assert result.metadata["channels"] == 2
    assert result.metadata["sample_rate"] == 44100
