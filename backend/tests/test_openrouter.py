"""OpenRouter client and translation tests with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.subtitles.parsers.srt import parse_srt
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError
from app.translation.openrouter.exchange_log import JobOpenRouterExchangeLog, job_openrouter_log_path
from app.translation.openrouter.prompts import format_batch, parse_batch_response
from app.translation.openrouter.service import OpenRouterTranslationService


SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Hello

2
00:00:04,000 --> 00:00:06,000
World
"""


@pytest.mark.asyncio
async def test_openrouter_success(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    client = OpenRouterClient("test-key")
    result = await client.chat_completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "ok"
    assert result.total_tokens == 2


@pytest.mark.asyncio
async def test_openrouter_auth_failure(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    client = OpenRouterClient("bad")
    with pytest.raises(OpenRouterError, match="authentication"):
        await client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_openrouter_rate_limit_then_success(monkeypatch):
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "done"}}], "usage": {}},
        )

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    async def _sleep(_delay=0):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _sleep)
    client = OpenRouterClient("key")
    result = await client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "done"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openrouter_logs_request_and_response(tmp_path: Path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    log_path = job_openrouter_log_path(tmp_path, 42)
    exchange_log = JobOpenRouterExchangeLog(log_path, job_id=42)
    exchange_log.record({"event": "job_start", "model": "test-model"})
    client = OpenRouterClient("secret-key", exchange_log=exchange_log)
    result = await client.chat_completion(
        model="test-model",
        messages=[{"role": "user", "content": "Hello subtitle"}],
    )
    assert result.content == "translated"

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["event"] == "job_start"
    assert lines[1]["event"] == "exchange"
    assert lines[1]["job_id"] == 42
    assert lines[1]["request"]["messages"][0]["content"] == "Hello subtitle"
    assert lines[1]["response"]["status_code"] == 200
    assert lines[1]["response"]["body"]["choices"][0]["message"]["content"] == "translated"
    assert "secret-key" not in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_openrouter_logs_malformed_response(tmp_path: Path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    log_path = job_openrouter_log_path(tmp_path, 7)
    exchange_log = JobOpenRouterExchangeLog(log_path, job_id=7)
    client = OpenRouterClient("key", exchange_log=exchange_log)
    with pytest.raises(OpenRouterError, match="Malformed"):
        await client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["response"]["body"] == {"unexpected": True}


def test_parse_batch_response():
    content = """[001]
Olá

[002]
Mundo
"""
    mapping = parse_batch_response(content)
    assert mapping[1] == "Olá"
    assert mapping[2] == "Mundo"
    assert "001" in format_batch([(1, "Hello")])


@pytest.mark.asyncio
async def test_translation_service_success(monkeypatch):
    async def fake_chat(*, model, messages, temperature=0.2, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        return ChatResult(
            content="[001]\nOlá\n\n[002]\nMundo\n",
            model=model,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    doc = parse_srt(SAMPLE)
    outcome = await service.translate_document(
        doc,
        model="openai/gpt-4o-mini",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=50,
    )
    assert outcome.document.blocks[0].text == "Olá"
    assert outcome.document.blocks[0].start == "00:00:01,000"
    assert outcome.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_translation_validation_retry(monkeypatch):
    calls = {"n": 0}

    async def fake_chat(*, model, messages, temperature=0.2, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        calls["n"] += 1
        if calls["n"] == 1:
            return ChatResult(content="broken", model=model, total_tokens=1)
        return ChatResult(
            content="[001]\nOlá\n\n[002]\nMundo\n",
            model=model,
            total_tokens=2,
        )

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="m",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
    )
    assert outcome.document.blocks[1].text == "Mundo"
    assert calls["n"] == 2
