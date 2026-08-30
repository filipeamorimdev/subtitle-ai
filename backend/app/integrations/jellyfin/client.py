"""Small async client for Jellyfin's media-library API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx


class JellyfinError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class JellyfinClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise JellyfinError("Jellyfin URL is not configured.")
        if not api_key:
            raise JellyfinError("Jellyfin API key is not configured.")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
                headers={"X-Emby-Token": self.api_key},
            ) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Could not connect to Jellyfin. {exc}") from exc
        if response.status_code in {401, 403}:
            raise JellyfinError("Jellyfin authentication failed.", status_code=response.status_code)
        if response.status_code >= 400:
            raise JellyfinError(
                f"Jellyfin request failed ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned an invalid response.") from exc

    async def test_connection(self) -> dict[str, Any]:
        data = await self._request("/System/Info")
        if not isinstance(data, dict):
            raise JellyfinError("Jellyfin returned an invalid system response.")
        return {
            "server_name": data.get("ServerName"),
            "version": data.get("Version"),
            "id": data.get("Id"),
        }

    async def list_media(self, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Return every playable movie and episode, following Jellyfin pagination."""
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            data = await self._request(
                "/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Episode",
                    "Fields": "Path,ProviderIds,SeriesInfo,ParentId",
                    "StartIndex": start,
                    "Limit": page_size,
                    "EnableTotalRecordCount": "true",
                },
            )
            if not isinstance(data, dict):
                raise JellyfinError("Jellyfin returned an invalid media response.")
            page = data.get("Items")
            if not isinstance(page, list):
                raise JellyfinError("Jellyfin returned an invalid media list.")
            valid = [item for item in page if isinstance(item, dict)]
            items.extend(valid)
            start += len(page)
            total = data.get("TotalRecordCount")
            try:
                total_count = int(total)
            except (TypeError, ValueError):
                total_count = None
            if (
                not page
                or len(page) < page_size
                or (total_count is not None and start >= total_count)
            ):
                break
        return items
