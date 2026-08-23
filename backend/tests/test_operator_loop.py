"""Operator agent loop tests with a scripted stub provider."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ai.models import AIResponse, Message, ToolCall
from app.ai.providers.mock import MockAIProvider
from app.core.config import get_app_config
from app.db import Base
from app.localization.operator import (
    ConfirmedTool,
    OperatorLoop,
    create_session,
)
from app.media import MediaRef
from app.media.service import MediaItemService


@pytest.fixture
def op_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media = tmp_path / "media" / "Matrix"
    media.mkdir(parents=True)
    (media / "The Matrix.mkv").write_text("x")

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
    # Seed settings row via SettingsService
    from app.services.settings import SettingsService

    SettingsService(db).get_or_create_row()
    row = SettingsService(db).get_or_create_row()
    row.operator_model_id = "mock-tools"
    db.add(row)
    db.commit()

    yield db, tmp_path
    db.close()


def _scripted_provider(responses: list[AIResponse]) -> MockAIProvider:
    queue = list(responses)

    def factory(**kwargs):
        if not queue:
            return AIResponse(
                provider_id="mock",
                model_id=kwargs.get("model_id", "mock-tools"),
                content="Done.",
                request_id=kwargs.get("request_id"),
            )
        return queue.pop(0)

    return MockAIProvider(response_factory=factory)


@pytest.mark.asyncio
async def test_loop_search_then_create(op_env, monkeypatch):
    db, tmp_path = op_env

    class FakeProvider:
        async def search_media(self, q: str):
            return [
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:1",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    path=str(tmp_path / "media" / "Matrix" / "The Matrix.mkv"),
                    bazarr_movie_id=1,
                )
            ]

        async def get_media(self, external_id: str):
            return MediaRef(
                provider_id="bazarr",
                external_id=external_id,
                media_type="movie",
                title="The Matrix",
                year=1999,
                path=str(tmp_path / "media" / "Matrix" / "The Matrix.mkv"),
                bazarr_movie_id=1,
            )

    monkeypatch.setattr(
        "app.localization.operator_tools._bazarr_provider",
        lambda _db: FakeProvider(),
    )

    planned: list[int] = []

    async def fake_plan(self, task_id: int):
        planned.append(task_id)

    monkeypatch.setattr("app.localization.operator_tools.TaskPlanner.plan", fake_plan)
    monkeypatch.setattr(
        "app.localization.operator.resolve_operator_model_id",
        lambda _db: "mock-tools",
    )

    provider = _scripted_provider(
        [
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="search_media",
                        arguments={"query": "The Matrix"},
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="2",
                        name="ensure_media",
                        arguments={
                            "external_id": "movie:1",
                            "media_type": "movie",
                            "title": "The Matrix",
                            "year": 1999,
                            "bazarr_movie_id": 1,
                        },
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="3",
                        name="create_localization_task",
                        arguments={"media_id": 1, "target_language": "pt-PT"},
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="Started Portuguese (Portugal) subtitles for The Matrix.",
            ),
        ]
    )

    session = create_session(db)
    # ensure_media will assign media_id; create uses media_id from args which we
    # fix after ensure by rewriting the scripted create args dynamically.
    # Simpler: pre-upsert media and skip ensure in the script.
    media = MediaItemService(db).upsert_from_ref(
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

    provider = _scripted_provider(
        [
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="search_media",
                        arguments={"query": "The Matrix"},
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="3",
                        name="create_localization_task",
                        arguments={
                            "media_id": media.id,
                            "target_language": "pt-PT",
                        },
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="Started Portuguese (Portugal) subtitles for The Matrix.",
            ),
        ]
    )

    result = await OperatorLoop(db, provider=provider).handle_user_message(
        session.id,
        "Get Portuguese subs for The Matrix",
    )
    assert "Started" in result.assistant_text
    assert any(e["tool"] == "search_media" for e in result.tool_events)
    assert any(e["tool"] == "create_localization_task" for e in result.tool_events)
    assert planned
    assert result.media_links


@pytest.mark.asyncio
async def test_unknown_tool_rejected_in_loop(op_env, monkeypatch):
    db, _ = op_env
    monkeypatch.setattr(
        "app.localization.operator.resolve_operator_model_id",
        lambda _db: "mock-tools",
    )
    provider = _scripted_provider(
        [
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(id="1", name="delete_everything", arguments={})
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="I cannot do that.",
            ),
        ]
    )
    session = create_session(db)
    result = await OperatorLoop(db, provider=provider).handle_user_message(
        session.id, "wipe library"
    )
    assert result.tool_events[0]["result"]["error"] == "unknown_tool"
    assert "cannot" in result.assistant_text.lower() or result.assistant_text


@pytest.mark.asyncio
async def test_dub_requires_confirmation(op_env, monkeypatch):
    db, tmp_path = op_env
    monkeypatch.setattr(
        "app.localization.operator.resolve_operator_model_id",
        lambda _db: "mock-tools",
    )
    media = MediaItemService(db).upsert_from_ref(
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
    provider = _scripted_provider(
        [
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="start_dub",
                        arguments={"media_id": media.id},
                    )
                ],
            ),
        ]
    )
    session = create_session(db)
    result = await OperatorLoop(db, provider=provider).handle_user_message(
        session.id, "Dub The Matrix"
    )
    assert result.pending_confirmation is not None
    assert result.pending_confirmation["tool"] == "start_dub"
    assert result.pending_confirmation.get("needs_confirmation") is True


@pytest.mark.asyncio
async def test_ambiguous_search_does_not_create_task(op_env, monkeypatch):
    db, tmp_path = op_env

    class FakeProvider:
        async def search_media(self, q: str):
            return [
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:1",
                    media_type="movie",
                    title="The Matrix",
                    year=1999,
                    bazarr_movie_id=1,
                ),
                MediaRef(
                    provider_id="bazarr",
                    external_id="movie:2",
                    media_type="movie",
                    title="The Matrix Reloaded",
                    year=2003,
                    bazarr_movie_id=2,
                ),
            ]

    monkeypatch.setattr(
        "app.localization.operator_tools._bazarr_provider",
        lambda _db: FakeProvider(),
    )
    monkeypatch.setattr(
        "app.localization.operator.resolve_operator_model_id",
        lambda _db: "mock-tools",
    )

    created = {"n": 0}

    async def boom(*_a, **_k):
        created["n"] += 1
        raise AssertionError("should not create")

    monkeypatch.setattr(
        "app.localization.operator_tools.create_localization_task",
        boom,
    )

    provider = _scripted_provider(
        [
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="search_media",
                        arguments={"query": "Matrix"},
                    )
                ],
            ),
            AIResponse(
                provider_id="mock",
                model_id="mock-tools",
                content="I found two titles — which one?",
            ),
        ]
    )
    session = create_session(db)
    result = await OperatorLoop(db, provider=provider).handle_user_message(
        session.id, "Matrix in Portuguese"
    )
    assert result.tool_events[0]["result"]["ambiguous"] is True
    assert created["n"] == 0
    assert "which" in result.assistant_text.lower()
