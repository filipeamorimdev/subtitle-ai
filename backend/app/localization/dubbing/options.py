"""Stable options shared by the dub API, job runner, and media pipeline."""

from __future__ import annotations

import re
from collections.abc import Mapping

DUB_MIX_BACKGROUND_PRESERVED = "background_preserved"
DUB_MIX_VOICEOVER_PREVIEW = "voiceover_preview"
DUB_MIX_MODES = frozenset(
    {
        DUB_MIX_BACKGROUND_PRESERVED,
        DUB_MIX_VOICEOVER_PREVIEW,
    }
)


def normalize_dub_mix_mode(value: str | None) -> str:
    """Return a supported mix mode, defaulting new jobs to the full mix."""
    mode = (value or DUB_MIX_BACKGROUND_PRESERVED).strip().lower()
    if mode not in DUB_MIX_MODES:
        choices = ", ".join(sorted(DUB_MIX_MODES))
        raise ValueError(f"Unsupported dub mix mode {value!r}; expected one of: {choices}")
    return mode


def speaker_key(value: str | None) -> str:
    """Normalize a subtitle speaker label for stable, case-insensitive matching."""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def cue_key(cue_index: int | None) -> str:
    """Stable key for an AI assignment tied to an individual SRT cue."""
    try:
        index = int(cue_index) if cue_index is not None else 0
    except (TypeError, ValueError):
        index = 0
    return f"cue:{index}" if index > 0 else ""


def normalize_speaker_voice_overrides(values: Mapping[str, str] | None) -> dict[str, str]:
    """Validate label or ``cue:N`` → Chatterbox profile mappings for a dub job."""
    normalized: dict[str, str] = {}
    for raw_speaker, raw_model in (values or {}).items():
        key = speaker_key(raw_speaker)
        model = str(raw_model or "").strip()
        if not key or not model:
            continue
        normalized[key] = model
    return normalized


def normalize_voice_bindings(values: Mapping[str, Mapping[str, object]] | None) -> dict[str, dict[str, object]]:
    """Validate immutable character voice bindings keyed by cue or speaker."""
    normalized: dict[str, dict[str, object]] = {}
    for raw_key, raw_binding in (values or {}).items():
        key = raw_key.strip()
        if not key or not isinstance(raw_binding, Mapping):
            continue
        reference = str(raw_binding.get("reference_relative_path") or "").strip()
        voice_model = str(raw_binding.get("voice_model") or "").strip()
        if not reference or not voice_model:
            continue
        normalized[key] = {
            "character_id": raw_binding.get("character_id"),
            "character_key": str(raw_binding.get("character_key") or ""),
            "display_name": str(raw_binding.get("display_name") or ""),
            "reference_relative_path": reference,
            "reference_sha256": str(raw_binding.get("reference_sha256") or ""),
            "voice_model": voice_model,
            "cfg_weight": raw_binding.get("cfg_weight"),
            "synthesis_seed": raw_binding.get("synthesis_seed"),
            "variant": str(raw_binding.get("variant") or "neutral"),
        }
    return normalized


def voice_binding_cache_fingerprint(bindings: Mapping[str, Mapping[str, object]] | None) -> dict[str, str]:
    """Compact per-binding hashes for cue-cache invalidation."""
    fingerprint: dict[str, str] = {}
    for key, binding in (bindings or {}).items():
        digest = str(binding.get("reference_sha256") or binding.get("reference_relative_path") or "")
        model = str(binding.get("voice_model") or "")
        cfg = binding.get("cfg_weight")
        fingerprint[key] = f"{digest}|{model}|{cfg}"
    return fingerprint
