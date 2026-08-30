"""Bazarr-backed MediaProvider for search and localization state."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any

from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.languages import get_language
from app.media import LanguageAvailability, LocalizationState, MediaRef
from app.subtitles.filenames import (
    detect_language_from_filename,
    language_chip_available,
    languages_compatible,
    normalize_language_code,
    suppress_generic_language_chip,
)

BAZARR_PROVIDER_ID = "bazarr"
_SEARCH_CACHE_TTL_SECONDS = 45.0
_SEARCH_CACHE: dict[str, tuple[float, list[MediaRef]]] = {}
_LOCALIZATION_CACHE_TTL_SECONDS = 10.0
_LOCALIZATION_CACHE: dict[str, tuple[float, LocalizationState]] = {}


def movie_external_id(radarr_id: int) -> str:
    return f"movie:{int(radarr_id)}"


def series_external_id(series_id: int) -> str:
    return f"series:{int(series_id)}"


def episode_external_id(episode_id: int) -> str:
    return f"episode:{int(episode_id)}"


def parse_external_id(external_id: str) -> tuple[str, int] | None:
    if not external_id or ":" not in external_id:
        return None
    kind, _, raw = external_id.partition(":")
    try:
        return kind, int(raw)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from(raw: dict[str, Any]) -> int | None:
    for key in ("year", "Year"):
        year = _as_int(raw.get(key))
        if year:
            return year
    # Sometimes buried in title "The Matrix (1999)"
    title = str(raw.get("title") or raw.get("name") or "")
    match = re.search(r"\((\d{4})\)\s*$", title)
    if match:
        return int(match.group(1))
    return None


def _metadata_from(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep compact casting context and external IDs from a Bazarr response.

    Bazarr may proxy Radarr/Sonarr identifiers, but it does not guarantee that
    every endpoint returns them.  We preserve what is present and leave any
    external lookup to a future, explicitly configured metadata provider.
    """
    metadata: dict[str, Any] = {"raw_keys": sorted(raw.keys())[:20]}
    aliases = {
        "imdb_id": ("imdbId", "imdb_id", "imdbid", "imdb"),
        "tmdb_id": ("tmdbId", "tmdb_id", "tmdbid", "tmdb"),
        "tvdb_id": ("tvdbId", "tvdb_id", "tvdbid", "tvdb"),
        "overview": ("overview", "plot", "description"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = raw.get(key)
            if not isinstance(value, (str, int)) or not str(value).strip():
                continue
            metadata[target] = str(value)[:800] if target == "overview" else value
            break
    return metadata


def _movie_ref(raw: dict[str, Any]) -> MediaRef | None:
    radarr_id = _as_int(
        raw.get("radarrId") or raw.get("radarrid") or raw.get("id")
    )
    if radarr_id is None:
        return None
    title = str(raw.get("title") or raw.get("name") or f"Movie {radarr_id}")
    return MediaRef(
        provider_id=BAZARR_PROVIDER_ID,
        external_id=movie_external_id(radarr_id),
        media_type="movie",
        title=title,
        year=_year_from(raw),
        path=str(raw.get("path") or raw.get("movie_path") or "") or None,
        bazarr_movie_id=radarr_id,
        metadata=_metadata_from(raw),
    )


def _series_ref(raw: dict[str, Any]) -> MediaRef | None:
    series_id = _as_int(
        raw.get("sonarrSeriesId") or raw.get("seriesId") or raw.get("id")
    )
    if series_id is None:
        return None
    title = str(raw.get("title") or raw.get("seriesTitle") or raw.get("name") or f"Series {series_id}")
    return MediaRef(
        provider_id=BAZARR_PROVIDER_ID,
        external_id=series_external_id(series_id),
        media_type="series",
        title=title,
        year=_year_from(raw),
        path=str(raw.get("path") or "") or None,
        bazarr_series_id=series_id,
        metadata=_metadata_from(raw),
    )


def _episode_ref(raw: dict[str, Any], *, series_title: str | None = None) -> MediaRef | None:
    episode_id = _as_int(
        raw.get("sonarrEpisodeId") or raw.get("episodeid") or raw.get("id")
    )
    series_id = _as_int(
        raw.get("sonarrSeriesId") or raw.get("seriesId") or raw.get("seriesid")
    )
    if episode_id is None:
        return None
    season = _as_int(raw.get("season") or raw.get("seasonNumber"))
    episode = _as_int(raw.get("episode") or raw.get("episodeNumber"))
    episode_title = str(raw.get("title") or raw.get("episodeTitle") or "") or None
    series_name = (
        series_title
        or str(raw.get("seriesTitle") or raw.get("series") or "")
        or None
    )
    parts: list[str] = []
    if series_name:
        parts.append(series_name)
    if season is not None and episode is not None:
        parts.append(f"S{season:02d}E{episode:02d}")
    if episode_title:
        parts.append(episode_title)
    title = " - ".join(parts) if parts else f"Episode {episode_id}"
    metadata = _metadata_from(raw)
    if series_name:
        metadata["series_title"] = series_name
    return MediaRef(
        provider_id=BAZARR_PROVIDER_ID,
        external_id=episode_external_id(episode_id),
        media_type="episode",
        title=title,
        year=_year_from(raw),
        season=season,
        episode=episode,
        episode_title=episode_title,
        path=str(raw.get("path") or "") or None,
        parent_external_id=series_external_id(series_id) if series_id is not None else None,
        bazarr_series_id=series_id,
        bazarr_episode_id=episode_id,
        metadata=metadata,
    )


def _matches_query(ref: MediaRef, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    haystacks = [
        ref.title,
        ref.episode_title or "",
        str(ref.year or ""),
        f"S{ref.season:02d}E{ref.episode:02d}" if ref.season is not None and ref.episode is not None else "",
    ]
    blob = " ".join(haystacks).lower()
    return all(token in blob for token in q.split())


class BazarrMediaProvider:
    """Media discovery via Bazarr (only concrete MediaProvider for v0.3)."""

    provider_id = BAZARR_PROVIDER_ID

    def __init__(self, client: BazarrClient) -> None:
        self.client = client

    async def search_media(self, query: str) -> list[MediaRef]:
        q = (query or "").strip()
        if len(q) < 2:
            return []
        cache_key = f"{self.provider_id}|{q.lower()}"
        cached = _SEARCH_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _SEARCH_CACHE_TTL_SECONDS:
            return list(cached[1])

        results: list[MediaRef] = []
        try:
            movies = await self.client.list_movies()
        except BazarrError:
            raise
        for raw in movies:
            ref = _movie_ref(raw)
            if ref and _matches_query(ref, q):
                results.append(ref)

        try:
            series_list = await self.client.list_series()
        except BazarrError:
            series_list = []

        matching_series: list[MediaRef] = []
        for raw in series_list:
            ref = _series_ref(raw)
            if ref and _matches_query(ref, q):
                matching_series.append(ref)
                results.append(ref)

        # Load episodes only for series that match the query (or all series if
        # query looks like an episode title without series hit — limited set).
        series_ids = [s.bazarr_series_id for s in matching_series if s.bazarr_series_id is not None]
        if not series_ids and series_list:
            # Fallback: check a small number of series when query might be episode-only.
            # Prefer not dumping the whole library: only series whose titles share a token.
            tokens = set(q.lower().split())
            for raw in series_list:
                ref = _series_ref(raw)
                if ref is None or ref.bazarr_series_id is None:
                    continue
                title_tokens = set(ref.title.lower().split())
                if tokens & title_tokens:
                    series_ids.append(ref.bazarr_series_id)
            series_ids = series_ids[:25]

        if series_ids:
            episodes = await self.client.get_episodes_by_series_ids(series_ids)
            series_titles = {
                s.bazarr_series_id: s.title for s in matching_series if s.bazarr_series_id is not None
            }
            for raw in episodes:
                sid = _as_int(raw.get("sonarrSeriesId") or raw.get("seriesId") or raw.get("seriesid"))
                ref = _episode_ref(raw, series_title=series_titles.get(sid) if sid else None)
                if ref and _matches_query(ref, q):
                    results.append(ref)

        # Prefer movies/episodes over bare series when titles collide.
        results.sort(
            key=lambda r: (
                0 if r.media_type == "movie" else 1 if r.media_type == "episode" else 2,
                r.title.lower(),
                r.season or 0,
                r.episode or 0,
            )
        )
        _SEARCH_CACHE[cache_key] = (now, list(results))
        return results

    async def get_media(self, external_id: str) -> MediaRef | None:
        parsed = parse_external_id(external_id)
        if parsed is None:
            return None
        kind, oid = parsed
        if kind == "movie":
            raw = await self.client.get_movie(oid)
            return _movie_ref(raw) if raw else None
        if kind == "episode":
            raw = await self.client.get_episode(oid)
            return _episode_ref(raw) if raw else None
        if kind == "series":
            series_list = await self.client.list_series()
            for raw in series_list:
                ref = _series_ref(raw)
                if ref and ref.bazarr_series_id == oid:
                    return ref
            return None
        return None

    async def get_localization_state(self, media: MediaRef) -> LocalizationState:
        """Subtitle presence from Bazarr metadata (audio not implemented)."""
        cache_key = (
            f"{media.media_type}:{media.bazarr_movie_id or ''}:{media.bazarr_episode_id or ''}"
        )
        cached = _LOCALIZATION_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _LOCALIZATION_CACHE_TTL_SECONDS:
            state = cached[1]
            return replace(state, languages=list(state.languages))

        languages: list[LanguageAvailability] = []
        raw: dict[str, Any] | None = None
        if media.media_type == "movie" and media.bazarr_movie_id is not None:
            raw = await self.client.get_movie(media.bazarr_movie_id)
        elif media.media_type == "episode" and media.bazarr_episode_id is not None:
            raw = await self.client.get_episode(media.bazarr_episode_id)

        present_codes: set[str] = set()
        missing_codes: set[str] = set()
        if raw:
            for sub in BazarrClient.parse_subtitles(raw):
                # Only count languages that have an actual subtitle file. Bazarr
                # sometimes lists configured/wanted languages with a display name
                # and no path (parse_subtitles then falls back to the name).
                if not _looks_like_subtitle_path(sub.path):
                    continue
                code = normalize_language_code(sub.language_code) or sub.language_code
                if code:
                    present_codes.add(code)
                file_lang = detect_language_from_filename(sub.path)
                if file_lang:
                    present_codes.add(file_lang)
            for miss in BazarrClient.parse_missing_languages(raw):
                code = normalize_language_code(miss) or miss
                if code:
                    missing_codes.add(code)

        # Include featured catalog languages for display; full catalog is the dropdown.
        from app.languages import list_featured_languages

        featured = list_featured_languages()
        featured_codes = [lang.code for lang in featured]
        seen: set[str] = set()
        for lang in featured:
            if lang.code in seen:
                continue
            seen.add(lang.code)
            available = language_chip_available(lang.code, present_codes)
            if available and suppress_generic_language_chip(lang.code, present_codes, featured_codes):
                available = False
            languages.append(
                LanguageAvailability(
                    language_code=lang.code,
                    language_name=lang.display_name,
                    available=available,
                )
            )
        # Also surface unknown present codes from Bazarr.
        for code in sorted(present_codes):
            if any(languages_compatible(code, s) for s in seen):
                continue
            meta = get_language(code)
            languages.append(
                LanguageAvailability(
                    language_code=code,
                    language_name=meta.display_name if meta else code,
                    available=True,
                )
            )
            seen.add(code)
        # Missing-only languages not in catalog yet
        for code in sorted(missing_codes):
            if any(languages_compatible(code, s) for s in seen):
                continue
            meta = get_language(code)
            languages.append(
                LanguageAvailability(
                    language_code=code,
                    language_name=meta.display_name if meta else code,
                    available=False,
                )
            )

        state = LocalizationState(capability="subtitles", languages=languages)
        _LOCALIZATION_CACHE[cache_key] = (now, state)
        return replace(state, languages=list(state.languages))


def _looks_like_subtitle_path(path: str | None) -> bool:
    if not path:
        return False
    lowered = path.replace("\\", "/").lower()
    return "/" in lowered or lowered.endswith(".srt")


def clear_search_cache() -> None:
    _SEARCH_CACHE.clear()
    _LOCALIZATION_CACHE.clear()
