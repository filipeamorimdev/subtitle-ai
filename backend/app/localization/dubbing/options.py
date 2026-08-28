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
