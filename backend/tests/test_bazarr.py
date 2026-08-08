"""Bazarr client and candidate tests."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas import PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.integrations.bazarr.client import BazarrClient
from app.integrations.bazarr.paths import PathMapping, apply_path_mapping
from app.services.candidates import CandidateService
from app.services.settings import SettingsService


def test_path_mapping():
    mappings = [PathMapping(bazarr_prefix="/movies", local_prefix="/media/movies")]
    assert apply_path_mapping("/movies/Foo/file.mkv", mappings) == "/media/movies/Foo/file.mkv"


@pytest.mark.asyncio
async def test_bazarr_wanted_and_connection(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/system/status"):
            return httpx.Response(200, json={"bazarr_version": "1.4.0"})
        if request.url.path.endswith("/api/movies/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "radarrId": 10,
                            "missing_subtitles": ["pt"],
                        }
                    ],
                    "total": 1,
                },
            )
        if request.url.path.endswith("/api/episodes/wanted"):
            return httpx.Response(200, json={"data": [], "total": 0})
        if request.url.path.endswith("/api/movies"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "path": "/movies/Example/Example.mkv",
                            "radarrId": 10,
                            "missing_subtitles": [
                                {"code2": "pt", "name": "Portuguese", "forced": False, "hi": False}
                            ],
                            "subtitles": [
                                {"code2": "en", "path": "/movies/Example/Example.en.srt", "name": "English"}
                            ],
                        }
                    ],
                    "total": 1,
                },
            )
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    client = BazarrClient("http://bazarr:6767", "secret")
    status = await client.test_connection()
    assert status["ok"] is True
    movies = await client.get_wanted_movies()
    details = await client.get_movies_by_ids([10])
    merged = client.merge_wanted_with_detail(movies[0], details[0])
    item = client.normalize_wanted_movie(merged)
    assert item.movie_id == 10
    assert item.path.endswith("Example.mkv")
    assert item.subtitles[0].language_code == "en"


@pytest.mark.asyncio
async def test_bazarr_download_subtitle(monkeypatch):
    seen: list[tuple[str, str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/api/movies/subtitles") and request.method == "PATCH":
            assert request.url.params["radarrid"] == "10"
            assert request.url.params["language"] == "en"
            assert request.url.params["forced"] == "false"
            assert request.url.params["hi"] == "false"
            return httpx.Response(204)
        if request.url.path.endswith("/api/episodes/subtitles") and request.method == "PATCH":
            assert request.url.params["seriesid"] == "3"
            assert request.url.params["episodeid"] == "22"
            assert request.url.params["language"] == "en"
            return httpx.Response(204)
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    client = BazarrClient("http://bazarr:6767", "secret")
    await client.download_movie_subtitle(10, "en")
    await client.download_episode_subtitle(3, 22, "en")
    assert len(seen) == 2
    assert seen[0][0] == "PATCH"
    assert seen[1][0] == "PATCH"


@pytest.mark.asyncio
async def test_request_subtitle_for_candidate(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    media_dir = tmp_path / "Empty"
    media_dir.mkdir()
    (media_dir / "Empty.mkv").write_text("x")

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    fernet = load_or_create_fernet(config_dir / "secret.key")
    settings = SettingsService(db, fernet=fernet)
    settings.update(
        SettingsUpdate(
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="k",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            media_roots=[str(tmp_path)],
            path_mappings=[PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path))],
        )
    )

    patch_calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/movies/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "No Source",
                            "radarrId": 11,
                            "missing_subtitles": [
                                {"code2": "pt", "name": "Portuguese", "forced": False, "hi": False}
                            ],
                        }
                    ],
                    "total": 1,
                },
            )
        if request.url.path.endswith("/api/episodes/wanted"):
            return httpx.Response(200, json={"data": [], "total": 0})
        if request.url.path.endswith("/api/movies"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "No Source",
                            "path": "/movies/Empty/Empty.mkv",
                            "radarrId": 11,
                            "subtitles": [],
                        }
                    ],
                    "total": 1,
                },
            )
        if request.url.path.endswith("/api/movies/subtitles") and request.method == "PATCH":
            patch_calls.append(dict(request.url.params))
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    service = CandidateService(db)
    candidates = await service.list_candidates()
    blocked = next(c for c in candidates if c.reason_code == "no_source")
    result = await service.request_subtitle(blocked.key)
    assert result.ok is True
    assert result.language == "en"
    assert patch_calls == [
        {"apikey": "k", "radarrid": "11", "language": "en", "forced": "false", "hi": "false"}
    ]


@pytest.mark.asyncio
async def test_candidate_service(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    media_dir = tmp_path / "Example"
    media_dir.mkdir()
    (media_dir / "Example.mkv").write_text("x")
    (media_dir / "Example.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n",
        encoding="utf-8",
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    fernet = load_or_create_fernet(config_dir / "secret.key")
    settings = SettingsService(db, fernet=fernet)
    settings.update(
        SettingsUpdate(
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="k",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            media_roots=[str(tmp_path)],
            path_mappings=[PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path))],
        )
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/movies/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "radarrId": 10,
                            "missing_subtitles": [
                                {"code2": "pt", "name": "Portuguese", "forced": False, "hi": False}
                            ],
                        },
                        {
                            "title": "No Source",
                            "radarrId": 11,
                            "missing_subtitles": [
                                {"code2": "pt", "name": "Portuguese", "forced": False, "hi": False}
                            ],
                        },
                    ],
                    "total": 2,
                },
            )
        if request.url.path.endswith("/api/episodes/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "seriesTitle": "Show",
                            "episodeTitle": "Pilot",
                            "episode_number": "1x1",
                            "sonarrEpisodeId": 22,
                            "sonarrSeriesId": 3,
                            "missing_subtitles": [
                                {"code2": "pt", "name": "Portuguese", "forced": False, "hi": False}
                            ],
                        }
                    ],
                    "total": 1,
                },
            )
        if request.url.path.endswith("/api/movies"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "path": "/movies/Example/Example.mkv",
                            "radarrId": 10,
                            "subtitles": [
                                {"code2": "en", "path": "/movies/Example/Example.en.srt"}
                            ],
                        },
                        {
                            "title": "No Source",
                            "path": "/movies/Empty/Empty.mkv",
                            "radarrId": 11,
                            "subtitles": [],
                        },
                    ],
                    "total": 2,
                },
            )
        if request.url.path.endswith("/api/episodes"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Pilot",
                            "path": "/movies/Example/Example.mkv",
                            "season": 1,
                            "episode": 1,
                            "sonarrEpisodeId": 22,
                            "sonarrSeriesId": 3,
                            "subtitles": [
                                {"code2": "en", "path": "/movies/Example/Example.en.srt"}
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    candidates = await CandidateService(db).list_candidates()
    assert len(candidates) == 3
    ready = [c for c in candidates if c.can_translate]
    blocked = [c for c in candidates if not c.can_translate]
    assert len(ready) == 2
    assert blocked[0].reason_code == "no_source"
    episode = next(c for c in candidates if c.media_type == "episode")
    assert "S01E01" in episode.title
