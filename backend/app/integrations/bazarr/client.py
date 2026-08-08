"""Bazarr HTTP client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.logging import get_logger

logger = get_logger("bazarr")


class BazarrError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class BazarrSubtitle:
    path: str | None
    language_code: str | None
    language_name: str | None = None
    forced: bool = False
    hi: bool = False


@dataclass
class BazarrWantedItem:
    media_type: str  # movie | episode
    title: str
    path: str
    movie_id: int | None = None
    episode_id: int | None = None
    series_id: int | None = None
    missing_languages: list[str] = field(default_factory=list)
    subtitles: list[BazarrSubtitle] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BazarrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise BazarrError("Bazarr URL is not configured")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key or ""
        self.timeout = timeout

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.api_key:
            params["apikey"] = self.api_key
        if extra:
            params.update(extra)
        return params

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    params=self._params(params),
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise BazarrError(f"Could not connect to Bazarr. {exc}") from exc

        if response.status_code == 401:
            raise BazarrError("Bazarr authentication failed.", status_code=401)
        if response.status_code >= 400:
            raise BazarrError(
                f"Bazarr request failed ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def test_connection(self) -> dict[str, Any]:
        # system endpoint is widely available
        data = await self._request("GET", "/api/system/status")
        if isinstance(data, dict):
            return {"ok": True, "data": data}
        # Fallback probe
        await self._request("GET", "/api/movies")
        return {"ok": True, "data": {"status": "reachable"}}

    async def get_wanted_movies(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/movies/wanted")
        return self._extract_list(data)

    async def get_wanted_episodes(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/episodes/wanted")
        return self._extract_list(data)

    async def get_movie(self, radarr_id: int) -> dict[str, Any] | None:
        data = await self._request("GET", "/api/movies", params={"radarrid[]": radarr_id})
        items = self._extract_list(data)
        return items[0] if items else None

    async def get_episode(self, episode_id: int) -> dict[str, Any] | None:
        data = await self._request("GET", "/api/episodes", params={"episodeid[]": episode_id})
        items = self._extract_list(data)
        return items[0] if items else None

    async def rescan_movie(self, radarr_id: int) -> None:
        # Best-effort endpoints used across Bazarr versions
        try:
            await self._request(
                "GET",
                "/api/movies/scan",
                params={"radarrid": radarr_id},
            )
            return
        except BazarrError:
            logger.warning("movies/scan failed; trying subtitles download sync endpoint")
        await self._request(
            "POST",
            "/api/movies/subtitles",
            params={"radarrid": radarr_id, "action": "scan"},
        )

    async def rescan_episode(self, episode_id: int) -> None:
        try:
            await self._request(
                "GET",
                "/api/episodes/scan",
                params={"episodeid": episode_id},
            )
            return
        except BazarrError:
            logger.warning("episodes/scan failed; trying alternate sync endpoint")
        await self._request(
            "POST",
            "/api/episodes/subtitles",
            params={"episodeid": episode_id, "action": "scan"},
        )

    @staticmethod
    def _extract_list(data: Any) -> list[dict[str, Any]]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "movies", "episodes", "wanted"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def parse_subtitles(raw_item: dict[str, Any]) -> list[BazarrSubtitle]:
        subs: list[BazarrSubtitle] = []
        raw_subs = raw_item.get("subtitles") or []
        if not isinstance(raw_subs, list):
            return subs
        for item in raw_subs:
            if isinstance(item, list) and len(item) >= 2:
                # Bazarr often returns [code, path] or [code, path, ...]
                code = str(item[0]) if item[0] is not None else None
                path = str(item[1]) if item[1] is not None else None
                subs.append(BazarrSubtitle(path=path, language_code=code))
            elif isinstance(item, dict):
                code = item.get("code2") or item.get("code3") or item.get("language")
                path = item.get("path") or item.get("name")
                subs.append(
                    BazarrSubtitle(
                        path=str(path) if path else None,
                        language_code=str(code) if code else None,
                        language_name=item.get("name"),
                        forced=bool(item.get("forced")),
                        hi=bool(item.get("hi")),
                    )
                )
        return subs

    @staticmethod
    def parse_missing_languages(raw_item: dict[str, Any]) -> list[str]:
        missing = raw_item.get("missing_subtitles") or raw_item.get("missingSubtitles") or []
        codes: list[str] = []
        if isinstance(missing, list):
            for item in missing:
                if isinstance(item, str):
                    codes.append(item)
                elif isinstance(item, dict):
                    code = item.get("code2") or item.get("code3") or item.get("name")
                    if code:
                        codes.append(str(code))
                elif isinstance(item, list) and item:
                    codes.append(str(item[0]))
        return codes

    def normalize_wanted_movie(self, item: dict[str, Any]) -> BazarrWantedItem:
        path = str(item.get("path") or item.get("movie_path") or "")
        title = str(item.get("title") or item.get("name") or path)
        movie_id = item.get("radarrId") or item.get("radarrid") or item.get("id")
        return BazarrWantedItem(
            media_type="movie",
            title=title,
            path=path,
            movie_id=int(movie_id) if movie_id is not None else None,
            missing_languages=self.parse_missing_languages(item),
            subtitles=self.parse_subtitles(item),
            raw=item,
        )

    def normalize_wanted_episode(self, item: dict[str, Any]) -> BazarrWantedItem:
        path = str(item.get("path") or "")
        series = item.get("seriesTitle") or item.get("series") or ""
        episode_title = item.get("title") or ""
        season = item.get("season")
        episode = item.get("episode")
        label_parts = [str(series)] if series else []
        if season is not None and episode is not None:
            label_parts.append(f"S{int(season):02d}E{int(episode):02d}")
        if episode_title:
            label_parts.append(str(episode_title))
        title = " - ".join(p for p in label_parts if p) or path
        episode_id = item.get("sonarrEpisodeId") or item.get("episodeid") or item.get("id")
        series_id = item.get("sonarrSeriesId") or item.get("seriesId")
        return BazarrWantedItem(
            media_type="episode",
            title=title,
            path=path,
            episode_id=int(episode_id) if episode_id is not None else None,
            series_id=int(series_id) if series_id is not None else None,
            missing_languages=self.parse_missing_languages(item),
            subtitles=self.parse_subtitles(item),
            raw=item,
        )
