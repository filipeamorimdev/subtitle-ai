"""Local Chatterbox Multilingual TTS provider.

Chatterbox runs entirely in the Subtitle AI container.  Its checkpoint is
downloaded lazily by the upstream library and cached in ``HF_HOME``; the image
sets that directory to the persisted ``/config`` volume.
"""

from __future__ import annotations

import asyncio
import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.localization.artifacts import AudioArtifact
from app.localization.dubbing.models import VoiceConfig
from app.localization.dubbing.timeline import CUE_SAMPLE_RATE

logger = get_logger("tts.chatterbox")


class TTSError(Exception):
    """A user-facing failure from the local text-to-speech provider."""


@dataclass(frozen=True)
class ChatterboxVoiceProfile:
    """A safe, reproducible expressive profile for the multilingual model."""

    id: str
    label: str
    language_id: str
    exaggeration: float
    cfg_weight: float
    temperature: float


_PROFILE_STYLES: tuple[tuple[str, float, float, float, str], ...] = (
    ("calm", 0.25, 0.60, 0.65, "calm"),
    ("natural", 0.50, 0.50, 0.75, "natural"),
    ("expressive", 0.72, 0.35, 0.80, "expressive"),
    ("dramatic", 0.88, 0.25, 0.85, "dramatic"),
)

_LANGUAGES: dict[str, tuple[str, str]] = {
    "pt-pt": ("pt", "Portuguese (Portugal)"),
    "pt-br": ("pt", "Portuguese (Brazil)"),
    "en": ("en", "English"),
    "en-us": ("en", "English (US)"),
    "en-gb": ("en", "English (UK)"),
    "es": ("es", "Spanish"),
    "fr": ("fr", "French"),
    "de": ("de", "German"),
    "it": ("it", "Italian"),
}

_MODEL_LOCK = threading.Lock()
_MODEL_BY_DEVICE: dict[str, Any] = {}


def _normalized_language(target_language: str) -> str:
    value = (target_language or "").strip().replace("_", "-").lower()
    return value or "en"


def _profile_prefix(target_language: str) -> tuple[str, str]:
    language = _normalized_language(target_language)
    language_id, label = _LANGUAGES.get(language, _LANGUAGES.get(language.split("-", 1)[0], ("en", "English")))
    return language_id, label


def _profile_id(target_language: str, style: str) -> str:
    return f"chatterbox-multilingual-v3:{_normalized_language(target_language)}:{style}"


def resolve_voice_model_for_language(target_language: str) -> str:
    """Return the standard Chatterbox profile for a target language."""
    return _profile_id(target_language, "natural")


def recommended_voice_models_for_language(target_language: str) -> list[tuple[str, str]]:
    """Return curated expressive Chatterbox profiles for the target language."""
    _language_id, language_label = _profile_prefix(target_language)
    return [
        (_profile_id(target_language, style), f"Chatterbox V3 · {label} ({language_label})")
        for style, _exaggeration, _cfg_weight, _temperature, label in _PROFILE_STYLES
    ]


def _legacy_voice_model(value: str) -> bool:
    """Allow already-saved drafts and queued jobs to move to Chatterbox safely."""
    lowered = value.casefold()
    return lowered.startswith(("pt_", "en_", "es_", "fr_", "de_", "it_"))


def resolve_voice_profile(voice_model: str | None, target_language: str) -> ChatterboxVoiceProfile:
    """Validate a UI-selected profile and retain a safe fallback for old jobs."""
    requested = (voice_model or "").strip()
    if not requested or _legacy_voice_model(requested):
        requested = resolve_voice_model_for_language(target_language)

    expected_prefix = "chatterbox-multilingual-v3:"
    if not requested.startswith(expected_prefix):
        raise TTSError(
            "Unknown Chatterbox voice profile. Select one of the Chatterbox profiles shown in the casting workspace."
        )
    parts = requested.split(":")
    if len(parts) != 3:
        raise TTSError("Invalid Chatterbox voice profile.")
    _prefix, language_code, requested_style = parts
    language_id, language_label = _profile_prefix(language_code)
    for style, exaggeration, cfg_weight, temperature, label in _PROFILE_STYLES:
        if requested_style == style:
            return ChatterboxVoiceProfile(
                id=_profile_id(language_code, style),
                label=f"Chatterbox V3 · {label} ({language_label})",
                language_id=language_id,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                temperature=temperature,
            )
    raise TTSError("Invalid Chatterbox voice profile.")


def suggest_voice_model_for_style(default_voice_model: str, voice_style: str) -> str:
    """Map the audio model's editable description to a conservative profile.

    This is intentionally only a first suggestion: the casting workspace keeps
    the selected Chatterbox profile fully editable before the job starts.
    """
    style = (voice_style or "").casefold()
    language_code = _normalized_language(default_voice_model.split(":")[1]) if ":" in default_voice_model else "en"
    dramatic_words = (
        "angry", "anger", "furious", "shout", "intense", "dramatic", "tense",
        "zang", "irritad", "grita", "dramát", "tenso",
    )
    expressive_words = (
        "young", "child", "bright", "energetic", "excited", "playful", "warm",
        "criança", "jovem", "alegre", "entusi", "energét", "brinc",
    )
    calm_words = (
        "calm", "quiet", "soft", "narrat", "gentle", "reserved",
        "calmo", "suave", "baixo", "narrad", "sereno",
    )
    if any(word in style for word in dramatic_words):
        return _profile_id(language_code, "dramatic")
    if any(word in style for word in expressive_words):
        return _profile_id(language_code, "expressive")
    if any(word in style for word in calm_words):
        return _profile_id(language_code, "calm")
    return _profile_id(language_code, "natural")


def _device() -> str:
    requested = os.getenv("SUBTITLE_AI_CHATTERBOX_DEVICE", "").strip().lower()
    if requested in {"cpu", "cuda"}:
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def load_chatterbox_model(*, device: str | None = None) -> Any:
    """Load one cached Multilingual V3 instance per execution device."""
    selected_device = device or _device()
    with _MODEL_LOCK:
        cached = _MODEL_BY_DEVICE.get(selected_device)
        if cached is not None:
            return cached
        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as exc:
            raise TTSError("chatterbox-tts is not installed") from exc
        try:
            model = ChatterboxMultilingualTTS.from_pretrained(
                device=selected_device,
                t3_model="v3",
            )
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Could not load Chatterbox Multilingual V3: {exc}") from exc
        if getattr(model, "conds", None) is None:
            raise TTSError("Chatterbox downloaded without its default voice conditions; retry the dub.")
        _MODEL_BY_DEVICE[selected_device] = model
        logger.info("chatterbox_model_loaded device=%s", selected_device)
        return model


def write_chatterbox_wav(
    model: Any,
    profile: ChatterboxVoiceProfile,
    text: str,
    output_wav: Path,
) -> None:
    """Generate a normal WAV that FFmpeg can place on the dialogue timeline."""
    try:
        import torchaudio
    except ImportError as exc:
        raise TTSError("torchaudio is not installed") from exc
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio = model.generate(
            text,
            language_id=profile.language_id,
            exaggeration=profile.exaggeration,
            cfg_weight=profile.cfg_weight,
            temperature=profile.temperature,
        )
        torchaudio.save(str(output_wav), audio.detach().cpu(), model.sr)
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"Chatterbox could not synthesize this cue: {exc}") from exc


def tts_output_ignores_text(samples: list[tuple[int, float]]) -> bool:
    """Detect a provider fault that yields fixed-length clips for every cue."""
    if len(samples) < 8:
        return False
    chars = [count for count, _duration in samples]
    durations = [duration for _count, duration in samples]
    if max(chars) - min(chars) < 20:
        return False
    return max(durations) - min(durations) < 0.25


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


class ChatterboxTTSProvider:
    name = "chatterbox"

    def __init__(self, model: Any, profile: ChatterboxVoiceProfile) -> None:
        self.model = model
        self.profile = profile

    async def synthesize(
        self,
        text: str,
        voice: VoiceConfig,
        language: str,
        *,
        output_path: Path,
        is_cancelled=None,
    ) -> AudioArtifact:
        if is_cancelled and is_cancelled():
            raise TTSError("Dub cancelled")
        cancelled = threading.Event()
        synth_task = asyncio.create_task(
            asyncio.to_thread(write_chatterbox_wav, self.model, self.profile, text, output_path)
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
                logger.warning("Could not remove cancelled Chatterbox output %s", output_path)

        try:
            await asyncio.shield(synth_task)
        except asyncio.CancelledError:
            cancelled.set()
            synth_task.add_done_callback(discard_cancelled_output)
            raise
        if is_cancelled and is_cancelled():
            cancelled.set()
            discard_cancelled_output(synth_task)
            raise TTSError("Dub cancelled")
        if not output_path.is_file() or output_path.stat().st_size < 64:
            raise TTSError("Chatterbox produced no audio for cue")
        duration = wav_duration_seconds(output_path)
        return AudioArtifact(
            path=str(output_path),
            duration=duration,
            sample_rate=getattr(self.model, "sr", CUE_SAMPLE_RATE),
            channels=1,
            provider=self.name,
            language=language,
            metadata={
                "voice_id": voice.voice_id,
                "speaker_id": voice.speaker_id,
                "profile": self.profile.id,
            },
        )
