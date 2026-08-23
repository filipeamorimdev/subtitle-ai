"""Operator tool registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ai.models import ToolSpec
from app.core.config import get_app_config
from app.db import Base
from app.db.models import MediaItemRow
from app.localization import operator_tools as ot
from app.localization.service import LocalizationTaskService
from app.media import MediaRef
from app.media.service import MediaItemService


@pytest.fixture
def tools_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media = tmp_path / "media" / "Matrix"
    media.mkdir(parents=True)
    (media / "The Matrix.mkv").write_text("x")
    (media / "The Matrix.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path / "media"))
    get_app_config.cache_clear()

    engine = create_engine(
        f"sqlite:///{config_dir / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import app.db as db_module

    db_module._engine = engine
    db_module._SessionLocal = SessionLocal

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_localization_tasks_active
                ON localization_tasks (media_item_id, target_language_code, capability)
                WHERE status IN (
                    'requested', 'planning', 'waiting_for_source', 'processing',
                    'verifying', 'awaiting_approval'
                )
                """
            )
        )

    db = SessionLocal()
    yield db, tmp_path
    db.close()


def test_tool_specs_are_openai_shaped():
    specs = ot.tool_specs()
    assert any(s.name == "search_media" for s in specs)
    assert any(s.name == "create_localization_task" for s in specs)
    for spec in specs:
        assert isinstance(spec, ToolSpec)
        payload = spec.to_openai_dict()
        assert payload["type"] == "function"
        assert "name" in payload["function"]


@pytest.mark.asyncio
async def test_unknown_tool_rejected(tools_env):
    db, _ = tools_env
    result = await ot.call_tool(db, "drop_database", {"force": True})
    assert result["error"] == "unknown_tool"
    assert "search_media" in result["available"]


@pytest.mark.asyncio
async def test_normalize_language_tool(tools_env):
    db, _ = tools_env
    result = await ot.call_tool(
        db, "normalize_language", {"language": "Portuguese (Portugal)"}
    )
    assert result["code"] == "pt-PT"
    assert "Portuguese" in result["display_name"]


@pytest.mark.asyncio
async def test_create_task_requires_media_id(tools_env, monkeypatch):
    db, tmp_path = tools_env
    media_svc = MediaItemService(db)
    row = media_svc.upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="movie:1",
            media_type="movie",
            title="The Matrix",
            year=1999,
            path=str(tmp_path / "media" / "Matrix" / "The Matrix.mkv"),
            bazarr_movie_id=1,
        )
    )

    planned: list[int] = []

    async def fake_plan(self, task_id: int):
        planned.append(task_id)

    monkeypatch.setattr(
        "app.localization.operator_tools.TaskPlanner.plan",
        fake_plan,
    )

    missing = await ot.call_tool(
        db, "create_localization_task", {"target_language": "pt-PT"}
    )
    assert missing["error"] == "invalid_arguments"

    result = await ot.call_tool(
        db,
        "create_localization_task",
        {"media_id": row.id, "target_language": "pt-PT"},
    )
    assert result.get("ok") is True
    assert result["task_id"]
    assert result["target_language_code"] == "pt-PT"
    assert planned == [result["task_id"]]

    again = await ot.call_tool(
        db,
        "create_localization_task",
        {"media_id": row.id, "target_language": "pt-PT"},
    )
    assert again["error"] == "active_task_exists"
    assert again["task_id"] == result["task_id"]


@pytest.mark.asyncio
async def test_dub_requires_confirmation(tools_env):
    db, tmp_path = tools_env
    media_svc = MediaItemService(db)
    row = media_svc.upsert_from_ref(
        MediaRef(
            provider_id="bazarr",
            external_id="movie:2",
            media_type="movie",
            title="The Matrix",
            year=1999,
            path=str(tmp_path / "media" / "Matrix" / "The Matrix.mkv"),
            bazarr_movie_id=2,
        )
    )
    preview = await ot.call_tool(db, "start_dub", {"media_id": row.id})
    assert preview.get("needs_confirmation") is True
    assert preview["tool"] == "start_dub"


@pytest.mark.asyncio
async def test_search_media_stub(tools_env, monkeypatch):
    db, _ = tools_env

    class FakeProvider:
        async def search_media(self, q: str):
            return [
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:9",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    bazarr_movie_id=9,
                ),
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:10",
                    media_type="movie",
                    title="The Matrix Reloaded",
                    year=2003,
                    bazarr_movie_id=10,
                ),
            ]

    monkeypatch.setattr(
        "app.localization.operator_tools._bazarr_provider",
        lambda _db: FakeProvider(),
    )
    result = await ot.call_tool(db, "search_media", {"query": "Matrix"})
    assert result["count"] == 2
    assert result["ambiguous"] is True
    assert result["results"][0]["title"] == "The Matrix"
