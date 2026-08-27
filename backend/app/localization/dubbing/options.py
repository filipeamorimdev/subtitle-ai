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


def normalize_speaker_voice_overrides(values: Mapping[str, str] | None) -> dict[str, str]:
    """Validate and normalize user-supplied subtitle-speaker → Piper model mappings."""
    normalized: dict[str, str] = {}
    for raw_speaker, raw_model in (values or {}).items():
        key = speaker_key(raw_speaker)
        model = str(raw_model or "").strip()
        if not key or not model:
            continue
        normalized[key] = model
    return normalized
