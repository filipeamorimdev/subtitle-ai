"""Jellyfin catalog client and normalization tests."""

from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas import SettingsUpdate
from app.db import Base
from app.integrations.jellyfin.client import JellyfinClient, JellyfinError
from app.media import MediaRef
from app.media.catalog import MediaCatalog
from app.media.jellyfin_provider import JellyfinMediaProvider
from app.services.settings import SettingsService


@pytest.mark.asyncio
async def test_jellyfin_connection_and_media_search() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Emby-Token"] == "secret"
        if request.url.path == "/System/Info":
            return httpx.Response(
                200, json={"ServerName": "Living Room", "Version": "10.10.7", "Id": "server"}
            )
        if request.url.path == "/Items":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "movie-id",
                            "Type": "Movie",
                            "Name": "Arrival",
                            "ProductionYear": 2016,
                            "Path": "/movies/Arrival/Arrival.mkv",
                            "ProviderIds": {"Tmdb": "329865"},
                        },
                        {
                            "Id": "episode-id",
                            "Type": "Episode",
                            "Name": "Roswell That Ends Well",
                            "SeriesName": "Futurama",
                            "SeriesId": "series-id",
                            "ParentIndexNumber": 3,
                            "IndexNumber": 19,
                            "Path": "/tv/Futurama/S03E19.mkv",
                        },
                    ],
                    "TotalRecordCount": 2,
                },
            )
        return httpx.Response(404)

    client = JellyfinClient(
        "http://jellyfin:8096", "secret", transport=httpx.MockTransport(handler)
    )
    details = await client.test_connection()
    assert details["server_name"] == "Living Room"

    provider = JellyfinMediaProvider(client)
    results = await provider.search_media("Futurama S03E19")
    assert len(results) == 1
    episode = results[0]
    assert episode.provider_id == "jellyfin"
    assert episode.external_id == "episode-id"
    assert episode.title == "Futurama - S03E19 - Roswell That Ends Well"
    assert episode.metadata["series_title"] == "Futurama"


@pytest.mark.asyncio
async def test_jellyfin_media_pagination() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("StartIndex", "0"))
        item = {"Id": f"movie-{start}", "Type": "Movie", "Name": f"Movie {start}"}
        return httpx.Response(
            200,
            json={"Items": [item] if start < 2 else [], "TotalRecordCount": 2},
        )

    client = JellyfinClient(
        "http://jellyfin:8096", "secret", transport=httpx.MockTransport(handler)
    )
    items = await client.list_media(page_size=1)
    assert [item["Id"] for item in items] == ["movie-0", "movie-1"]


@pytest.mark.asyncio
async def test_catalog_falls_back_to_bazarr_when_jellyfin_is_unavailable() -> None:
    class UnavailableJellyfin:
        async def search_media(self, _query: str):
            raise JellyfinError("offline")

    class WorkingBazarr:
        async def search_media(self, _query: str):
            return [
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:1",
                    media_type="movie",
                    title="Fallback movie",
                )
            ]

    catalog = MediaCatalog(
        jellyfin=UnavailableJellyfin(),  # type: ignore[arg-type]
        bazarr=WorkingBazarr(),  # type: ignore[arg-type]
    )
    source, results = await catalog.search("fallback")
    assert source == "bazarr"
    assert [item.title for item in results] == ["Fallback movie"]


def test_jellyfin_settings_are_encrypted_and_masked() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        service = SettingsService(db, Fernet(Fernet.generate_key()))
        public = service.update(
            SettingsUpdate(
                jellyfin_url="http://jellyfin:8096/",
                jellyfin_api_key="super-secret-key",
            )
        )
        assert public.jellyfin_url == "http://jellyfin:8096/"
        assert public.jellyfin_api_key_configured is True
        assert public.jellyfin_api_key_masked != "super-secret-key"
        assert service.get_jellyfin_credentials() == (
            "http://jellyfin:8096/",
            "super-secret-key",
        )
    finally:
        db.close()
