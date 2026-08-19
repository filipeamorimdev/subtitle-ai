"""Auth and live event bus."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_app_config
from app.core.events import publish, subscribe, unsubscribe
from app.main import create_app


async def test_event_bus_delivers_payload():
    queue = await subscribe()
    try:
        hello = await queue.get()
        assert "hello" in hello
        publish({"type": "job", "job_id": 7})
        payload = await queue.get()
        assert '"job_id": 7' in payload
    finally:
        await unsubscribe(queue)


def test_health_and_openapi_include_events(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    get_app_config.cache_clear()
    import app.db as db_module

    db_module._engine = None
    db_module._SessionLocal = None
    app = create_app()
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert "events" in client.get("/openapi.json").json()["paths"]
        assert client.get("/api/nope").status_code == 404
