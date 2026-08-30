"""Jellyfin-backed movie and episode catalog."""

from __future__ import annotations

import time
from typing import Any

from app.integrations.jellyfin.client import JellyfinClient
from app.media import MediaRef

JELLYFIN_PROVIDER_ID = "jellyfin"
_CACHE_TTL_SECONDS = 60.0
_LIBRARY_CACHE: dict[str, tuple[float, list[MediaRef]]] = {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _media_ref(raw: dict[str, Any]) -> MediaRef | None:
    item_id = str(raw.get("Id") or "").strip()
    item_type = str(raw.get("Type") or "").lower()
    if not item_id or item_type not in {"movie", "episode"}:
        return None
    name = str(raw.get("Name") or "").strip()
    provider_ids = raw.get("ProviderIds") if isinstance(raw.get("ProviderIds"), dict) else {}
    metadata: dict[str, Any] = {"jellyfin_provider_ids": provider_ids}
    if item_type == "movie":
        return MediaRef(
            provider_id=JELLYFIN_PROVIDER_ID,
            external_id=item_id,
            media_type="movie",
            title=name or "Untitled movie",
            year=_as_int(raw.get("ProductionYear")),
            path=str(raw.get("Path") or "") or None,
            metadata=metadata,
        )

    series_name = str(raw.get("SeriesName") or "").strip()
    season = _as_int(raw.get("ParentIndexNumber"))
    episode = _as_int(raw.get("IndexNumber"))
    parts = [series_name] if series_name else []
    if season is not None and episode is not None:
        parts.append(f"S{season:02d}E{episode:02d}")
    if name:
        parts.append(name)
    if series_name:
        metadata["series_title"] = series_name
    series_id = str(raw.get("SeriesId") or "").strip() or None
    return MediaRef(
        provider_id=JELLYFIN_PROVIDER_ID,
        external_id=item_id,
        media_type="episode",
        title=" - ".join(parts) or "Untitled episode",
        year=_as_int(raw.get("ProductionYear")),
        season=season,
        episode=episode,
        episode_title=name or None,
        path=str(raw.get("Path") or "") or None,
        parent_external_id=series_id,
        metadata=metadata,
    )


def _matches(ref: MediaRef, query: str) -> bool:
    series_title = str(ref.metadata.get("series_title") or "")
    episode_code = (
        f"S{ref.season:02d}E{ref.episode:02d}"
        if ref.season is not None and ref.episode is not None
        else ""
    )
    text = " ".join(
        [ref.title, ref.episode_title or "", series_title, str(ref.year or ""), episode_code]
    ).lower()
    return all(token in text for token in query.lower().split())


class JellyfinMediaProvider:
    provider_id = JELLYFIN_PROVIDER_ID

    def __init__(self, client: JellyfinClient) -> None:
        self.client = client

    async def _library(self) -> list[MediaRef]:
        key = self.client.base_url
        cached = _LIBRARY_CACHE.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return list(cached[1])
        refs = [ref for raw in await self.client.list_media() if (ref := _media_ref(raw))]
        refs.sort(key=lambda ref: (ref.title.lower(), ref.season or -1, ref.episode or -1))
        _LIBRARY_CACHE[key] = (now, refs)
        return list(refs)

    async def search_media(self, query: str) -> list[MediaRef]:
        q = (query or "").strip()
        if len(q) < 2:
            return []
        return [ref for ref in await self._library() if _matches(ref, q)][:100]

    async def get_media(self, external_id: str) -> MediaRef | None:
        wanted = (external_id or "").strip()
        return next((ref for ref in await self._library() if ref.external_id == wanted), None)
