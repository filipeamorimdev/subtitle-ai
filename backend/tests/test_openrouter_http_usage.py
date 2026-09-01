"""Every OpenRouter HTTP attempt is persisted to ai_usage_records."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_app_config
from app.db import Base
from app.db.models import AiUsageRecordRow, SettingsRow
from app.services.ai_usage import AiUsageService, RecordingAIProvider, make_openrouter_http_usage_hook
from app.ai.providers.openrouter import OpenRouterProvider
from app.translation.openrouter.client import BatchChatRequest, OpenRouterClient


def _db(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="openai/gpt-4o-mini"))
    db.commit()
    return db


def _patch_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


@pytest.mark.asyncio
async def test_usage_hook_records_chat_retries(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    attempts = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _no_sleep)
    _patch_httpx(monkeypatch, handler)
    usage = AiUsageService(db)
    hook = make_openrouter_http_usage_hook(
        usage,
        job_id=None,
        trigger_type="manual",
        default_operation="translation",
    )
    client = OpenRouterClient("test-key", usage_hook=hook)
    result = await client.chat_completion(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional audiovisual subtitle translator."},
            {"role": "user", "content": "hi"},
        ],
    )
    assert result.content == "ok"
    rows = list(db.scalars(select(AiUsageRecordRow).order_by(AiUsageRecordRow.id)).all())
    assert len(rows) == 2
    assert rows[0].status == "failed"
    assert rows[0].failure_category == "http_429"
    assert rows[1].status == "success"
    assert rows[1].operation_type == "translation"
    assert rows[1].total_tokens == 2
    assert rows[0].request_id == rows[1].request_id


@pytest.mark.asyncio
async def test_usage_hook_marks_malformed_2xx_responses_failed(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "openai/gpt-4o-mini", "choices": []})

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _no_sleep)
    _patch_httpx(monkeypatch, handler)
    hook = make_openrouter_http_usage_hook(
        AiUsageService(db), job_id=9, trigger_type="manual"
    )
    with pytest.raises(Exception, match="Malformed OpenRouter response"):
        await OpenRouterClient("test-key", usage_hook=hook).chat_completion(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
        )

    rows = list(db.scalars(select(AiUsageRecordRow).order_by(AiUsageRecordRow.id)).all())
    assert len(rows) == 3
    assert all(row.status == "failed" and row.failure_category == "malformed" for row in rows)
    assert len({row.request_id for row in rows}) == 1


@pytest.mark.asyncio
async def test_recording_provider_counts_batch_submit_and_polls(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    polls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/batches"):
            return httpx.Response(
                200,
                json={
                    "id": "batch_xyz",
                    "status": "validating",
                    "request_counts": {"total": 1, "completed": 0, "failed": 0},
                },
            )
        if request.method == "GET" and "/batches/batch_xyz" in request.url.path:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "batch_xyz",
                        "status": "in_progress",
                        "request_counts": {"total": 1, "completed": 0, "failed": 0},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "batch_xyz",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "c1",
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "model": "openai/gpt-4o-mini",
                                    "choices": [{"message": {"content": "1\nok\n"}}],
                                    "usage": {
                                        "prompt_tokens": 5,
                                        "completion_tokens": 2,
                                        "total_tokens": 7,
                                    },
                                },
                            },
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    _patch_httpx(monkeypatch, handler)
    provider = OpenRouterProvider(db, api_key="test-key")
    recording = RecordingAIProvider(
        provider,
        AiUsageService(db),
        job_id=7,
        trigger_type="manual",
    )
    results = await recording.run_chat_batch(
        model_id="openai/gpt-4o-mini:batch",
        requests=[
            BatchChatRequest(
                custom_id="c1",
                messages=[{"role": "user", "content": "hi"}],
            )
        ],
        poll_interval_s=0,
    )
    assert "c1" in results
    rows = list(db.scalars(select(AiUsageRecordRow).order_by(AiUsageRecordRow.id)).all())
    ops = [r.operation_type for r in rows]
    # submit + 2 polls + 1 batch item generation
    assert ops.count("batch_submit") == 1
    assert ops.count("batch_poll") == 2
    assert ops.count("translation") == 1
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_list_models_records_catalog_list(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o-mini",
                        "name": "Mini",
                        "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                    }
                ]
            },
        )

    _patch_httpx(monkeypatch, handler)
    hook = make_openrouter_http_usage_hook(
        AiUsageService(db),
        job_id=None,
        trigger_type="system",
        default_operation="catalog_list",
    )
    models = await OpenRouterClient.list_models(api_key="test-key", usage_hook=hook)
    assert len(models) == 1
    rows = list(db.scalars(select(AiUsageRecordRow)).all())
    assert len(rows) == 1
    assert rows[0].operation_type == "catalog_list"
    assert rows[0].status == "success"


def test_usage_write_failure_is_deferred_and_replayed(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    usage = AiUsageService(db)
    original_record = usage.record
    calls = {"count": 0}

    def fail_once(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("database is locked")
        return original_record(**kwargs)

    monkeypatch.setattr(usage, "record", fail_once)
    prepared = {
        "model_id": "openai/gpt-4o-mini",
        "operation_type": "translation",
        "trigger_type": "manual",
        "job_id": None,
        "status": "success",
        "failure_category": None,
        "outcome": None,
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
        "actual_cost_usd": 0.0,
        "tier": "free",
        "provider_id": "openrouter",
        "request_id": "deferred-request",
        "attempt_number": 1,
    }

    usage.record_http_attempt(prepared)
    outbox = tmp_path / "config" / "logs" / "openrouter-usage-outbox.jsonl"
    assert outbox.exists()
    assert "messages" not in outbox.read_text(encoding="utf-8")
    assert list(db.scalars(select(AiUsageRecordRow)).all()) == []

    assert usage.replay_pending_http_attempts() == 1
    rows = list(db.scalars(select(AiUsageRecordRow)).all())
    assert len(rows) == 1
    assert rows[0].request_id == "deferred-request"
    assert not outbox.exists()
