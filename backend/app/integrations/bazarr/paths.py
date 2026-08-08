"""Path mapping utilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathMapping:
    bazarr_prefix: str
    local_prefix: str


def apply_path_mapping(path: str, mappings: list[PathMapping]) -> str:
    normalized = path.replace("\\", "/")
    # Longest prefix first
    ordered = sorted(mappings, key=lambda m: len(m.bazarr_prefix), reverse=True)
    for mapping in ordered:
        src = mapping.bazarr_prefix.rstrip("/")
        dst = mapping.local_prefix.rstrip("/")
        if normalized == src or normalized.startswith(src + "/"):
            suffix = normalized[len(src) :]
            return dst + suffix
    return normalized


def reverse_path_mapping(path: str, mappings: list[PathMapping]) -> str:
    normalized = path.replace("\\", "/")
    ordered = sorted(mappings, key=lambda m: len(m.local_prefix), reverse=True)
    for mapping in ordered:
        src = mapping.local_prefix.rstrip("/")
        dst = mapping.bazarr_prefix.rstrip("/")
        if normalized == src or normalized.startswith(src + "/"):
            suffix = normalized[len(src) :]
            return dst + suffix
    return normalized


def is_under_roots(path: str | Path, roots: list[str]) -> bool:
    resolved = Path(path).resolve()
    for root in roots:
        root_path = Path(root).resolve()
        try:
            resolved.relative_to(root_path)
            return True
        except ValueError:
            continue
    return False


def mappings_from_settings(raw: list[dict] | None) -> list[PathMapping]:
    result: list[PathMapping] = []
    for item in raw or []:
        bazarr_prefix = str(item.get("bazarr_prefix") or item.get("from") or "").strip()
        local_prefix = str(item.get("local_prefix") or item.get("to") or "").strip()
        if bazarr_prefix and local_prefix:
            result.append(PathMapping(bazarr_prefix=bazarr_prefix, local_prefix=local_prefix))
    return result
