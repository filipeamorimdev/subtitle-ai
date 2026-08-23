"""Structured pipeline decision records for tasks and job logs."""

from __future__ import annotations

from typing import Any, Mapping

PIPELINE_META_KEY = "pipeline"


def merge_pipeline_metadata(
    metadata: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge pipeline observability fields without touching checkpoints."""
    meta = dict(metadata or {})
    current = dict(meta.get(PIPELINE_META_KEY) or {}) if isinstance(meta.get(PIPELINE_META_KEY), dict) else {}
    current.update(dict(updates))
    meta[PIPELINE_META_KEY] = current
    return meta


def read_pipeline_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (metadata or {}).get(PIPELINE_META_KEY) if metadata else None
    return dict(raw) if isinstance(raw, dict) else {}
