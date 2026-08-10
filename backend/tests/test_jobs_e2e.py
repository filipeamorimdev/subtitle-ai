"""Job lifecycle and end-to-end mocked workflow tests."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base, get_db
from app.jobs.service import JobService
from app.main import create_app
from app.services.settings import SettingsService
from app.translation.openrouter.client import ChatResult


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello

2
00:00:04,000 --> 00:00:06,000
World
"""


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "media" / "Example"
    media.mkdir(parents=True)
    (media / "Example.mkv").write_text("x")
    source = media / "Example.en.srt"
    source.write_text(SAMPLE_SRT, encoding="utf-8")

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    engine = create_engine(f"sqlite:///{config_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Point app DB to test engine
    import app.db as db_module

    db_module._engine = engine
    db_module._SessionLocal = SessionLocal

    fernet = load_or_create_fernet(config_dir / "secret.key")
    db = SessionLocal()
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="baz",
            openrouter_api_key="or-key",
            openrouter_model="openai/gpt-4o-mini",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            media_roots=[str(tmp_path / "media")],
            path_mappings=[
                PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path / "media"))
            ],
            batch_size=50,
        )
    )
    db.close()

    async def bazarr_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/system/status"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/api/movies/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "radarrId": 10,
                            "missing_subtitles": ["pt-PT"],
                        }
                    ],
                    "total": 1,
                },
            )
        if path.endswith("/api/episodes/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "seriesTitle": "Show",
                            "episodeTitle": "Pilot",
                            "episode_number": "1x1",
                            "sonarrEpisodeId": 22,
                            "missing_subtitles": ["pt-PT"],
                        }
                    ],
                    "total": 1,
                },
            )
        if path.endswith("/api/movies"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "path": "/movies/Example/Example.mkv",
                            "radarrId": 10,
                            "subtitles": [["en", "/movies/Example/Example.en.srt"]],
                        }
                    ],
                    "total": 1,
                },
            )
        if path.endswith("/api/episodes"):
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
                            "subtitles": [["en", "/movies/Example/Example.en.srt"]],
                        }
                    ]
                },
            )
        if "scan" in path or path.endswith("/api/movies/subtitles") or path.endswith(
            "/api/episodes/subtitles"
        ):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, text=path)

    transport = httpx.MockTransport(bazarr_handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    async def fake_chat(self, *, model, messages, temperature=0.2, max_tokens=None):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "Classify media into a franchise universe" in system:
            content = '{"universe":"none"}'
        elif "extract audiovisual glossary terms" in system:
            content = (
                '{"terms":[{"source":"Example","target":"Exemplo",'
                '"type":"title","policy":"localize"}]}'
            )
        else:
            content = "[001]\nOlá\n\n[002]\nMundo\n"
        return ChatResult(
            content=content,
            model=model,
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )

    monkeypatch.setattr(
        "app.translation.openrouter.client.OpenRouterClient.chat_completion",
        fake_chat,
    )

    app = create_app()

    def override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    # Disable background worker side effects by not starting lifespan jobs twice:
    # TestClient triggers lifespan; worker will process jobs — good for e2e.

    return {
        "app": app,
        "SessionLocal": SessionLocal,
        "source": source,
        "media": media,
        "tmp_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_job_create_process_and_dedupe(app_env, monkeypatch):
    # Prevent worker race: process manually
    monkeypatch.setattr("app.jobs.worker.worker.start", lambda: None)
    monkeypatch.setattr("app.jobs.worker.worker.stop", lambda: None)

    # Recreate app without relying on worker; call process_job directly
    SessionLocal = app_env["SessionLocal"]
    db = SessionLocal()
    service = JobService(db)
    job = await service.create_job(
        JobCreate(
            source_subtitle_path=str(app_env["source"]),
            target_language="pt-PT",
            media_type="movie",
            media_path=str(app_env["media"] / "Example.mkv"),
            media_title="Example Movie",
            bazarr_movie_id=10,
            source_language="en",
        )
    )
    assert job.status == "pending"
    claimed = service.claim_next_job()
    assert claimed is not None
    await service.process_job(claimed.id)
    done = service.get_job(claimed.id)
    assert done is not None
    assert done.status == "completed"
    target = app_env["media"] / "Example.pt-PT.srt"
    assert target.exists()
    assert "Olá" in target.read_text(encoding="utf-8")
    assert app_env["source"].read_text(encoding="utf-8") == SAMPLE_SRT

    skipped = await service.create_job(
        JobCreate(
            source_subtitle_path=str(app_env["source"]),
            target_language="pt-PT",
            media_type="movie",
            media_path=str(app_env["media"] / "Example.mkv"),
            media_title="Example Movie",
            bazarr_movie_id=10,
            source_language="en",
        )
    )
    assert skipped.status == "skipped"
    assert skipped.reason_code == "target_exists"
    db.close()


def test_api_candidates_and_manual_job(app_env, monkeypatch):
    # Stop worker auto-claim during HTTP create; we process explicitly if needed
    async def noop_start():
        return None

    async def noop_stop():
        return None

    monkeypatch.setattr("app.jobs.worker.worker.start", noop_start)
    monkeypatch.setattr("app.jobs.worker.worker.stop", noop_stop)

    # Rebuild app so lifespan uses patched worker
    get_app_config.cache_clear()
    from app.main import create_app
    from app.db import get_db

    app = create_app()
    SessionLocal = app_env["SessionLocal"]

    def override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.1.0"

        settings = client.get("/api/settings")
        assert settings.status_code == 200
        body = settings.json()
        assert body["openrouter_api_key_configured"] is True
        assert "or-key" not in str(body)
        assert body["openrouter_api_key_masked"] is not None

        candidates = client.post("/api/candidates/refresh")
        assert candidates.status_code == 200
        items = candidates.json()
        assert len(items) == 2
        assert all("key" in c for c in items)
        movie = next(c for c in items if c["media_type"] == "movie")
        assert movie["can_translate"] is True

        # Ensure no jobs exist before explicit translate
        assert client.get("/api/jobs").json() == []

        created = client.post("/api/jobs", json={"candidate_key": movie["key"]})
        assert created.status_code == 200
        job = created.json()
        assert job["status"] in {"pending", "processing", "completed", "skipped"}

        # Process pending job if needed
        jobs = client.get("/api/jobs").json()
        assert len(jobs) >= 1
        job = jobs[0]
        if job["status"] == "pending":
            db = SessionLocal()
            service = JobService(db)
            claimed = service.claim_next_job()
            assert claimed is not None

            import asyncio

            asyncio.run(service.process_job(claimed.id))
            db.close()

        jobs = client.get("/api/jobs").json()
        assert any(j["status"] == "completed" for j in jobs)
        completed = next(j for j in jobs if j["status"] == "completed")
        log_response = client.get(f"/api/jobs/{completed['id']}/log")
        assert log_response.status_code == 200
        log_body = log_response.json()
        assert log_body["job_id"] == completed["id"]
        assert log_body["exists"] is True
        assert log_body["entry_count"] >= 2
        assert any(entry.get("event") == "job_start" for entry in log_body["entries"])
        assert any(entry.get("event") == "job_end" for entry in log_body["entries"])

        actions_response = client.get(f"/api/jobs/{completed['id']}/actions")
        assert actions_response.status_code == 200
        actions = actions_response.json()
        assert len(actions) >= 1
        assert any(a["id"] == completed["id"] and a["current"] is True for a in actions)
        assert all(
            {"id", "action", "status", "datetime", "duration_seconds", "message", "current"} <= set(a)
            for a in actions
        )
        current_action = next(a for a in actions if a["id"] == completed["id"])
        assert current_action["duration_seconds"] is None or current_action["duration_seconds"] >= 0

        stats = client.get("/api/stats").json()
        assert stats["total"] >= 1
        assert stats["completed"] >= 1
