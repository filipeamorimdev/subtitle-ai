"""Piper TTS provider (local ONNX voices)."""

from __future__ import annotations

import asyncio
import threading
import wave
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.localization.artifacts import AudioArtifact
from app.localization.dubbing.models import VoiceConfig
from app.localization.dubbing.timeline import CUE_SAMPLE_RATE

logger = get_logger("tts.piper")

PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_PATTERN = __import__("re").compile(
    r"^(?P<lang_family>[^-]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$"
)


class TTSError(Exception):
    pass


def piper_voice_download_urls(voice_model: str) -> tuple[str, str, str]:
    match = VOICE_PATTERN.match(voice_model.strip())
    if not match:
        raise TTSError(
            f"Invalid Piper voice model: {voice_model} "
            "(expected format like en_US-lessac-medium)"
        )
    lang_family = match.group("lang_family")
    lang_code = f"{lang_family}_{match.group('lang_region')}"
    voice_name = match.group("voice_name")
    voice_quality = match.group("voice_quality")
    voice_code = f"{lang_code}-{voice_name}-{voice_quality}"

    def build_url(extension: str) -> str:
        filename = f"{voice_code}{extension}"
        segments = [lang_family, lang_code, voice_name, voice_quality, filename]
        path = "/".join(quote(segment, safe="") for segment in segments)
        return f"{PIPER_VOICES_BASE}/{path}?download=true"

    return voice_code, build_url(".onnx"), build_url(".onnx.json")


async def _download_piper_file(url: str, dest: Path, *, is_cancelled) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".partial")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(1800.0)) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with temp.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if is_cancelled and is_cancelled():
                            raise TTSError("Dub cancelled")
                        handle.write(chunk)
        temp.replace(dest)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


async def ensure_piper_voice_available(
    *,
    voice_model: str,
    voices_dir: Path,
    is_cancelled,
) -> Path:
    voices_dir.mkdir(parents=True, exist_ok=True)
    voice_code, model_url, config_url = piper_voice_download_urls(voice_model)
    model_path = voices_dir / f"{voice_code}.onnx"
    config_path = voices_dir / f"{voice_code}.onnx.json"
    if not (model_path.is_file() and model_path.stat().st_size > 0 and config_path.is_file()):
        logger.info("Downloading Piper voice model=%s into %s", voice_model, voices_dir)
        if not model_path.is_file() or model_path.stat().st_size <= 0:
            await _download_piper_file(model_url, model_path, is_cancelled=is_cancelled)
        if not config_path.is_file() or config_path.stat().st_size <= 0:
            await _download_piper_file(config_url, config_path, is_cancelled=is_cancelled)
    return model_path


def load_piper_voice(model_path: Path) -> Any:
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise TTSError("piper-tts is not installed") from exc
    try:
        return PiperVoice.load(str(model_path))
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"Failed to load Piper voice {model_path.name}: {exc}") from exc


def write_piper_wav(voice: Any, text: str, output_wav: Path) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as wav_file:
        synthesize_wav = getattr(voice, "synthesize_wav", None)
        if callable(synthesize_wav):
            synthesize_wav(text, wav_file)
            return
        try:
            voice.synthesize(text, wav_file)
            return
        except TypeError:
            pass
        params_set = False
        for chunk in voice.synthesize(text):
            if not params_set:
                wav_file.setframerate(getattr(chunk, "sample_rate", CUE_SAMPLE_RATE))
                wav_file.setsampwidth(getattr(chunk, "sample_width", 2))
                wav_file.setnchannels(getattr(chunk, "sample_channels", 1))
                params_set = True
            audio = getattr(chunk, "audio_int16_bytes", None)
            if audio:
                wav_file.writeframes(audio)


def piper_output_ignores_text(samples: list[tuple[int, float]]) -> bool:
    if len(samples) < 8:
        return False
    chars = [count for count, _duration in samples]
    durations = [duration for _count, duration in samples]
    if max(chars) - min(chars) < 20:
        return False
    return max(durations) - min(durations) < 0.25


def resolve_voice_model_for_language(target_language: str) -> str:
    lang = (target_language or "").strip()
    normalized = lang.replace("_", "-")
    if normalized.lower() == "pt-pt":
        return "pt_PT-tugão-medium"
    if normalized.lower() == "pt-br":
        return "pt_BR-faber-medium"
    return "en_US-lessac-medium"


def wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        if rate <= 0:
            return None
        return frames / float(rate)
    except Exception:
        return None


class PiperTTSProvider:
    name = "piper"

    def __init__(self, voice: Any | None = None) -> None:
        self.voice = voice

    async def synthesize(
        self,
        text: str,
        voice: VoiceConfig,
        language: str,
        *,
        output_path: Path,
        is_cancelled=None,
    ) -> AudioArtifact:
        if self.voice is None:
            raise TTSError("Piper voice is not loaded")
        if is_cancelled and is_cancelled():
            raise TTSError("Dub cancelled")
        cancelled = threading.Event()
        synth_task = asyncio.create_task(
            asyncio.to_thread(write_piper_wav, self.voice, text, output_path)
        )

        def discard_cancelled_output(done: asyncio.Task[None]) -> None:
            if not cancelled.is_set():
                return
            try:
                done.result()
            except Exception:  # noqa: BLE001
                pass
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove cancelled Piper output %s", output_path)

        try:
            await asyncio.shield(synth_task)
        except asyncio.CancelledError:
            # Piper synthesis is synchronous inside a thread.  Let it finish
            # without leaking its late output into a cancelled/retried job.
            cancelled.set()
            synth_task.add_done_callback(discard_cancelled_output)
            raise
        if is_cancelled and is_cancelled():
            cancelled.set()
            discard_cancelled_output(synth_task)
            raise TTSError("Dub cancelled")
        if not output_path.is_file() or output_path.stat().st_size < 64:
            raise TTSError("piper produced no audio for cue")
        duration = wav_duration_seconds(output_path)
        return AudioArtifact(
            path=str(output_path),
            duration=duration,
            sample_rate=CUE_SAMPLE_RATE,
            channels=1,
            provider=self.name,
            language=language,
            metadata={"voice_id": voice.voice_id, "speaker_id": voice.speaker_id},
        )
