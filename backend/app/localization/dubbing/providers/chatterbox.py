"""Local Chatterbox Multilingual TTS provider.

Chatterbox runs entirely in the Subtitle AI container.  The generic V3
checkpoint is downloaded lazily by the upstream library and cached in
``HF_HOME``; the image sets that directory to the persisted ``/config``
volume.  European Portuguese uses the official pt-PT T3 overlay plus the
shared companion weights from the base multilingual repo.
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
_MODEL_BY_DEVICE: dict[str, tuple[Any, str | None]] = {}
_COND_PREP_LOCK = threading.Lock()

_PT_PT_REPO_ID = "ResembleAI/Chatterbox-Multilingual-pt-pt"
_CHATTERBOX_REPO_ID = "ResembleAI/chatterbox"
_PT_PT_T3_FILENAME = "t3_pt_pt.safetensors"
_PT_PT_OVERLAY_FILES = (
    _PT_PT_T3_FILENAME,
    "grapheme_mtl_merged_expanded_v1.json",
)
_PT_PT_COMPANION_FILES = ("ve.pt", "conds.pt", "s3gen.pt")
_PT_PT_REQUIRED_FILES = (
    "ve.pt",
    "conds.pt",
    "s3gen.pt",
    _PT_PT_T3_FILENAME,
    "grapheme_mtl_merged_expanded_v1.json",
)
# S3Tokenizer builds these STFT buffers in ``__init__``. Official and V3
# checkpoints omit them; multilingual ``from_local`` still loads with
# ``strict=True``, unlike English Chatterbox which already uses ``strict=False``.
_S3GEN_OPTIONAL_STATE_KEYS = ("tokenizer._mel_filters", "tokenizer.window")


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


def _use_pt_pt_checkpoint(target_language: str | None) -> bool:
    language = _normalized_language(target_language or "")
    if language != "pt-pt":
        return False
    flag = os.getenv("SUBTITLE_AI_CHATTERBOX_PT_PT", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _pt_pt_cache_root() -> Path:
    from app.core.config import get_app_config

    cache_root = get_app_config().config_dir / "huggingface" / "chatterbox-pt-pt"
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def _pt_pt_checkpoint_complete(cache_root: Path) -> bool:
    return all((cache_root / name).is_file() for name in _PT_PT_REQUIRED_FILES)


def _download_snapshot(repo_id: str, cache_root: Path, allow_patterns: tuple[str, ...]) -> None:
    from huggingface_hub import snapshot_download

    needed = [name for name in allow_patterns if not (cache_root / name).is_file()]
    if not needed:
        return
    logger.info("chatterbox_pt_pt_download repo=%s files=%s", repo_id, ",".join(needed))
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(cache_root),
        allow_patterns=list(needed),
        token=os.getenv("HF_TOKEN"),
    )


def _s3gen_is_v3_overlay(cache_root: Path) -> bool:
    dest = cache_root / "s3gen.pt"
    overlay = cache_root / "s3gen_v3.pt"
    if not dest.is_file() or not overlay.is_file():
        return False
    try:
        if dest.samefile(overlay):
            return True
    except OSError:
        pass
    return dest.stat().st_size == overlay.stat().st_size


def _discard_incompatible_s3gen(cache_root: Path) -> None:
    dest = cache_root / "s3gen.pt"
    if not dest.is_file() or not _s3gen_is_v3_overlay(cache_root):
        return
    logger.info("chatterbox_pt_pt_discard_s3gen_v3_alias")
    dest.unlink()


def _optional_s3gen_state_keys(module: Any) -> tuple[str, ...]:
    """Return checkpoint keys that are safe to restore from the live module."""
    keys: list[str] = []
    keys.extend(getattr(module, "ignore_state_dict_missing", ()) or ())
    tokenizer = getattr(module, "tokenizer", None)
    for name in getattr(tokenizer, "ignore_state_dict_missing", ()) or ():
        keys.append(f"tokenizer.{name}")
    keys.extend(_S3GEN_OPTIONAL_STATE_KEYS)
    unique: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return tuple(unique)


def _merge_optional_s3gen_buffers(module: Any, state_dict: Any) -> Any:
    """Copy init-time tokenizer buffers into a checkpoint that omitted them."""
    if not isinstance(state_dict, dict):
        return state_dict
    current = module.state_dict()
    merged = dict(state_dict)
    for key in _optional_s3gen_state_keys(module):
        if key not in merged and key in current:
            merged[key] = current[key]
    return merged


def _install_s3gen_checkpoint_compat() -> None:
    """Make multilingual S3Gen loading tolerate omitted tokenizer buffers."""
    try:
        from chatterbox.models.s3gen import S3Gen
    except ImportError:
        return
    if getattr(S3Gen.load_state_dict, "_subtitle_ai_compat", False):
        return

    def load_state_dict(self, state_dict, *args, **kwargs):
        import torch

        return torch.nn.Module.load_state_dict(
            self,
            _merge_optional_s3gen_buffers(self, state_dict),
            *args,
            **kwargs,
        )

    load_state_dict._subtitle_ai_compat = True
    S3Gen.load_state_dict = load_state_dict


def _ensure_pt_pt_checkpoint() -> Path:
    """Assemble the pt-PT overlay with the shared Multilingual V3 companions.

    ``ResembleAI/Chatterbox-Multilingual-pt-pt`` only ships the language T3
    and tokenizer.  The pinned Chatterbox loader still needs ``ve.pt``,
    ``conds.pt``, and ``s3gen.pt`` from ``ResembleAI/chatterbox``.  Prefer the
    official decoder over a leftover ``s3gen_v3.pt`` alias; both files omit
    tokenizer STFT buffers that are filled back in at load time.
    """
    cache_root = _pt_pt_cache_root()
    _discard_incompatible_s3gen(cache_root)
    if _pt_pt_checkpoint_complete(cache_root):
        return cache_root
    _download_snapshot(_PT_PT_REPO_ID, cache_root, _PT_PT_OVERLAY_FILES)
    _download_snapshot(_CHATTERBOX_REPO_ID, cache_root, _PT_PT_COMPANION_FILES)
    missing = [name for name in _PT_PT_REQUIRED_FILES if not (cache_root / name).is_file()]
    if missing:
        raise TTSError(
            "Chatterbox pt-PT checkpoint is incomplete after download: " + ", ".join(missing)
        )
    return cache_root


def load_chatterbox_model(
    *,
    device: str | None = None,
    target_language: str | None = None,
) -> Any:
    """Load one cached Multilingual V3 instance per execution device."""
    selected_device = device or _device()
    pt_pt = _use_pt_pt_checkpoint(target_language)
    cache_key = f"{selected_device}:{'pt-pt' if pt_pt else 'multilingual'}"
    with _MODEL_LOCK:
        cached = _MODEL_BY_DEVICE.get(cache_key)
        if cached is not None:
            return cached[0]
        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ModuleNotFoundError as exc:
            if exc.name == "chatterbox":
                raise TTSError(
                    "Chatterbox TTS is not installed in this server image. Deploy the current Subtitle AI image, then retry the dub."
                ) from exc
            if exc.name == "chatterbox.mtl_tts":
                raise TTSError(
                    "The installed Chatterbox version does not support Multilingual V3. Deploy the current Subtitle AI image, then retry the dub."
                ) from exc
            raise TTSError(f"Chatterbox could not be imported: {exc}") from exc
        except ImportError as exc:
            raise TTSError(f"Chatterbox could not be imported: {exc}") from exc
        try:
            _install_s3gen_checkpoint_compat()
            if pt_pt:
                ckpt_dir = _ensure_pt_pt_checkpoint()
                model = ChatterboxMultilingualTTS.from_local(
                    ckpt_dir,
                    selected_device,
                    t3_model=_PT_PT_T3_FILENAME,
                )
            else:
                model = ChatterboxMultilingualTTS.from_pretrained(
                    device=selected_device,
                    t3_model="v3",
                )
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"Could not load Chatterbox Multilingual V3: {exc}") from exc
        if getattr(model, "conds", None) is None:
            raise TTSError("Chatterbox downloaded without its default voice conditions; retry the dub.")
        _MODEL_BY_DEVICE[cache_key] = (model, target_language)
        logger.info(
            "chatterbox_model_loaded device=%s checkpoint=%s",
            selected_device,
            "pt-pt" if pt_pt else "multilingual-v3",
        )
        return model


def unload_chatterbox_model(*, device: str | None = None) -> None:
    """Drop cached model references so a long CPU job can reload cleanly."""
    selected_device = device or _device()
    with _MODEL_LOCK:
        keys = [key for key in _MODEL_BY_DEVICE if key.startswith(f"{selected_device}:")]
        removed = [_MODEL_BY_DEVICE.pop(key, None) for key in keys]
    for entry in removed:
        if entry is not None:
            del entry[0]
    if removed:
        logger.info("chatterbox_model_unloaded device=%s count=%s", selected_device, len(removed))


def write_chatterbox_wav(
    model: Any,
    profile: ChatterboxVoiceProfile,
    text: str,
    output_wav: Path,
    *,
    audio_prompt_path: str | Path | None = None,
    cfg_weight: float | None = None,
    exaggeration: float | None = None,
    temperature: float | None = None,
) -> None:
    """Generate a normal WAV that FFmpeg can place on the dialogue timeline."""
    try:
        import torchaudio
    except ImportError as exc:
        raise TTSError("torchaudio is not installed") from exc
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    prompt = Path(audio_prompt_path) if audio_prompt_path else None
    if prompt is not None and not prompt.is_file():
        raise TTSError(f"Voice reference audio is missing: {prompt.name}")
    generate_kwargs = {
        "language_id": profile.language_id,
        "exaggeration": profile.exaggeration if exaggeration is None else exaggeration,
        "cfg_weight": profile.cfg_weight if cfg_weight is None else cfg_weight,
        "temperature": profile.temperature if temperature is None else temperature,
    }
    try:
        with _COND_PREP_LOCK:
            if prompt is not None:
                model.prepare_conditionals(
                    str(prompt),
                    exaggeration=generate_kwargs["exaggeration"],
                )
            audio = model.generate(text, **generate_kwargs)
        # The timeline probes each cue with Python's wave module.  Explicit
        # PCM S16 keeps every generated WAV readable there as well as by
        # ffmpeg; torchaudio's backend defaults can otherwise create an
        # extensible/float WAV that loses duration information.
        torchaudio.save(
            str(output_wav),
            audio.detach().cpu(),
            model.sr,
            format="wav",
            encoding="PCM_S",
            bits_per_sample=16,
        )
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

    def __init__(
        self,
        model: Any,
        profile: ChatterboxVoiceProfile,
        *,
        reference_wav: str | Path | None = None,
        cfg_weight: float | None = None,
    ) -> None:
        self.model = model
        self.profile = profile
        self.reference_wav = Path(reference_wav) if reference_wav else None
        self.cfg_weight = cfg_weight

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
        reference = voice.metadata.get("reference_wav") or (
            str(self.reference_wav) if self.reference_wav is not None else None
        )
        cfg_weight = voice.metadata.get("cfg_weight", self.cfg_weight)
        cancelled = threading.Event()
        synth_task = asyncio.create_task(
            asyncio.to_thread(
                write_chatterbox_wav,
                self.model,
                self.profile,
                text,
                output_path,
                audio_prompt_path=reference,
                cfg_weight=cfg_weight,
            )
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
