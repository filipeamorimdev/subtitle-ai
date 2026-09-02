"""Safe on-disk layout for character reference audio."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_app_config

_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._-]+")


def voices_root() -> Path:
    root = get_app_config().config_dir / "voices"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_segment(value: str, *, fallback: str = "item") -> str:
    cleaned = _SAFE_SEGMENT.sub("-", (value or "").strip()).strip("-").lower()
    return cleaned[:80] or fallback


def series_voice_dir(media_item_id: int, *, slug: str | None = None) -> Path:
    label = _safe_segment(slug or f"media-{media_item_id}")
    path = voices_root() / f"{media_item_id}-{label}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def character_dir(series_dir: Path, character_key: str) -> Path:
    path = series_dir / _safe_segment(character_key, fallback="character")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_reference_path(relative_path: str) -> Path:
    """Resolve a stored relative path and reject escapes outside the voices root."""
    root = voices_root().resolve()
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Voice reference path escapes the configured voices directory.")
    return candidate


def relative_reference_path(absolute: Path) -> str:
    root = voices_root().resolve()
    resolved = absolute.resolve()
    if root not in resolved.parents:
        raise ValueError("Voice reference must live under the voices directory.")
    return str(resolved.relative_to(root))
