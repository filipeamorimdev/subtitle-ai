"""OpenRouter client and translation tests with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.ai.errors import AIProviderError
from app.subtitles.parsers.srt import parse_srt
from app.translation.openrouter.client import (
    OpenRouterClient,
    OpenRouterError,
    batch_base_model,
    is_batch_model,
)
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


def test_is_batch_model_and_base_slug():
    assert is_batch_model("openai/gpt-4o-mini:batch")
    assert not is_batch_model("openai/gpt-4o-mini")
    assert not is_batch_model("openai/gpt-4o-mini:batching")
    assert batch_base_model("openai/gpt-4o-mini:batch") == "openai/gpt-4o-mini"
    assert batch_base_model("openai/gpt-4o-mini") == "openai/gpt-4o-mini"


def _patch_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


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

    _patch_httpx(monkeypatch, handler)
    client = OpenRouterClient("test-key")
    result = await client.chat_completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "ok"
    assert result.total_tokens == 2


@pytest.mark.asyncio
async def test_chat_completion_with_tool_calls(monkeypatch):
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen["tools"] = payload.get("tools")
        seen["tool_choice"] = payload.get("tool_choice")
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "search_media",
                                        "arguments": '{"query":"Matrix"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            },
        )

    _patch_httpx(monkeypatch, handler)
    client = OpenRouterClient("test-key")
    result = await client.chat_completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Find The Matrix"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_media",
                    "description": "Search media",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ],
        tool_choice="auto",
    )
    assert result.content == ""
    assert result.tool_calls is not None
    assert result.tool_calls[0]["function"]["name"] == "search_media"
    assert seen["tool_choice"] == "auto"
    assert seen["tools"][0]["function"]["name"] == "search_media"


@pytest.mark.asyncio
async def test_chat_completion_strips_batch_suffix(monkeypatch):
    seen = {"model": None}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen["model"] = payload["model"]
        assert "/chat/completions" in str(request.url)
        assert "/api/beta/" not in str(request.url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    _patch_httpx(monkeypatch, handler)
    client = OpenRouterClient("key")
    await client.chat_completion(
        model="openai/gpt-4o-mini:batch",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert seen["model"] == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_openrouter_auth_failure(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    _patch_httpx(monkeypatch, handler)
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

    _patch_httpx(monkeypatch, handler)

    async def _sleep(_delay=0):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _sleep)
    client = OpenRouterClient("key")
    result = await client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "done"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openrouter_list_models_sorted_by_price(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.url.params["sort"] == "pricing-low-to-high"
        assert request.url.params["output_modalities"] == "text"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "expensive/model",
                        "name": "Expensive",
                        "pricing": {"prompt": "0.000002", "completion": "0.000004"},
                        "context_length": 8000,
                    },
                    {
                        "id": "cheap/model",
                        "name": "Cheap",
                        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
                        "context_length": 16000,
                    },
                    {
                        "id": "free/model",
                        "name": "Free",
                        "pricing": {"prompt": "0", "completion": "0"},
                        "context_length": 4000,
                    },
                ]
            },
        )

    _patch_httpx(monkeypatch, handler)
    models = await OpenRouterClient.list_models(api_key="test-key")
    assert [m.id for m in models] == ["free/model", "cheap/model", "expensive/model"]
    assert models[0].prompt_price_per_million == 0.0
    assert models[1].prompt_price_per_million == pytest.approx(0.1)
    assert models[2].completion_price_per_million == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_run_chat_batch_submit_and_poll(monkeypatch):
    polls = {"n": 0}
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.method == "POST" and request.url.path.endswith("/batches"):
            # Stream-parse order: endpoint and model before requests.
            raw = request.content.decode("utf-8")
            assert raw.index('"endpoint"') < raw.index('"requests"')
            assert raw.index('"model"') < raw.index('"requests"')
            payload = json.loads(raw)
            assert payload["endpoint"] == "/v1/chat/completions"
            assert payload["model"] == "openai/gpt-4o-mini"
            assert payload["requests"][0]["custom_id"] == "chunk-1"
            assert payload["requests"][0]["body"]["model"] == "openai/gpt-4o-mini"
            return httpx.Response(
                202,
                json={
                    "id": "batch_123",
                    "status": "validating",
                    "request_counts": {"total": 1, "completed": 0, "failed": 0},
                    "results": None,
                },
            )
        if request.method == "GET" and "/batches/batch_123" in request.url.path:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "id": "batch_123",
                        "status": "in_progress",
                        "request_counts": {"total": 1, "completed": 0, "failed": 0},
                        "results": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "batch_123",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "id": "batch_req_1",
                            "custom_id": "chunk-1",
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "model": "openai/gpt-4o-mini",
                                    "choices": [
                                        {"message": {"content": "[001]\nOlá\n\n[002]\nMundo\n"}}
                                    ],
                                    "usage": {
                                        "prompt_tokens": 10,
                                        "completion_tokens": 5,
                                        "total_tokens": 15,
                                    },
                                },
                            },
                            "error": None,
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    _patch_httpx(monkeypatch, handler)

    async def _sleep(_delay=0):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _sleep)

    from app.translation.openrouter.client import BatchChatRequest

    client = OpenRouterClient("key")
    results = await client.run_chat_batch(
        model="openai/gpt-4o-mini:batch",
        requests=[
            BatchChatRequest(
                custom_id="chunk-1",
                messages=[{"role": "user", "content": "hi"}],
            )
        ],
        poll_interval_s=0,
    )
    assert results["chunk-1"].content.startswith("[001]")
    assert results["chunk-1"].total_tokens == 15
    assert any("/api/beta/batches" in url for url in urls)
    assert polls["n"] == 2


@pytest.mark.asyncio
async def test_translation_service_uses_openrouter_batch_for_batch_model(monkeypatch):
    urls: list[str] = []
    sync_calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.method == "POST" and request.url.path.endswith("/batches"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "openai/gpt-4o-mini"
            assert len(payload["requests"]) == 1
            return httpx.Response(
                202,
                json={
                    "id": "batch_abc",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "chunk-1",
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "choices": [
                                        {
                                            "message": {
                                                "content": "[001]\nOlá\n\n[002]\nMundo\n"
                                            }
                                        }
                                    ],
                                    "usage": {"total_tokens": 9},
                                },
                            },
                            "error": None,
                        }
                    ],
                },
            )
        if "/chat/completions" in request.url.path:
            sync_calls["n"] += 1
            raise AssertionError("sync chat should not run when batch results are valid")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    _patch_httpx(monkeypatch, handler)

    async def _sleep(_delay=0):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _sleep)

    client = OpenRouterClient("key")
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="openai/gpt-4o-mini:batch",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=50,
    )
    assert [b.text for b in outcome.document.blocks] == ["Olá", "Mundo"]
    assert outcome.model == "openai/gpt-4o-mini:batch"
    assert outcome.usage.total_tokens == 9
    assert any("/api/beta/batches" in url for url in urls)
    assert sync_calls["n"] == 0


@pytest.mark.asyncio
async def test_translation_batch_model_repairs_via_sync(monkeypatch):
    urls: list[str] = []
    sync_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.method == "POST" and request.url.path.endswith("/batches"):
            return httpx.Response(
                202,
                json={
                    "id": "batch_fix",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "chunk-1",
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "choices": [{"message": {"content": "[001]\nOlá\n"}}],
                                    "usage": {"total_tokens": 1},
                                },
                            },
                            "error": None,
                        }
                    ],
                },
            )
        if "/chat/completions" in request.url.path:
            payload = json.loads(request.content.decode("utf-8"))
            sync_models.append(payload["model"])
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "[002]\nMundo\n"}}],
                    "usage": {"total_tokens": 2},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    _patch_httpx(monkeypatch, handler)

    async def _sleep(_delay=0):
        return None

    monkeypatch.setattr("app.translation.openrouter.client.asyncio.sleep", _sleep)

    client = OpenRouterClient("key")
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="openai/gpt-4o-mini:batch",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=50,
    )
    assert [b.text for b in outcome.document.blocks] == ["Olá", "Mundo"]
    assert any("/api/beta/batches" in url for url in urls)
    assert any("/chat/completions" in url for url in urls)
    assert sync_models
    assert all(model == "openai/gpt-4o-mini" for model in sync_models)


@pytest.mark.asyncio
async def test_translation_non_batch_model_never_hits_batches_api(monkeypatch):
    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        return ChatResult(
            content="[001]\nOlá\n\n[002]\nMundo\n",
            model=model,
            total_tokens=3,
        )

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)

    async def boom(*_args, **_kwargs):
        raise AssertionError("run_chat_batch should not be called for sync models")

    monkeypatch.setattr(client, "run_chat_batch", boom)

    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="openai/gpt-4o-mini",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=50,
    )
    assert outcome.document.blocks[0].text == "Olá"


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

    _patch_httpx(monkeypatch, handler)

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
    assert lines[1]["request"]["message_count"] == 1
    assert lines[1]["response"]["status_code"] == 200
    assert lines[1]["response"]["body_omitted"] is True
    assert "Hello subtitle" not in log_path.read_text(encoding="utf-8")
    assert "secret-key" not in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_openrouter_logs_full_bodies_when_enabled(tmp_path: Path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    _patch_httpx(monkeypatch, handler)

    log_path = job_openrouter_log_path(tmp_path, 43)
    exchange_log = JobOpenRouterExchangeLog(log_path, job_id=43)
    client = OpenRouterClient(
        "secret-key",
        exchange_log=exchange_log,
        log_full_exchanges=True,
    )
    await client.chat_completion(
        model="test-model",
        messages=[{"role": "user", "content": "Hello subtitle"}],
    )
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["request"]["messages"][0]["content"] == "Hello subtitle"
    assert lines[0]["response"]["body"]["choices"][0]["message"]["content"] == "translated"


@pytest.mark.asyncio
async def test_openrouter_logs_malformed_response(tmp_path: Path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    _patch_httpx(monkeypatch, handler)

    log_path = job_openrouter_log_path(tmp_path, 7)
    exchange_log = JobOpenRouterExchangeLog(log_path, job_id=7)
    client = OpenRouterClient("key", exchange_log=exchange_log, log_full_exchanges=True)
    with pytest.raises(OpenRouterError, match="Malformed"):
        await client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3  # retries malformed responses
    assert lines[0]["response"]["body"] == {"unexpected": True}
    assert all(line["response"]["body"] == {"unexpected": True} for line in lines)


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
    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
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

    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        calls["n"] += 1
        if calls["n"] == 1:
            return ChatResult(content="broken", model=model, total_tokens=1)
        assert "missing subtitle blocks" in messages[-1]["content"].lower()
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


@pytest.mark.asyncio
async def test_translation_targeted_repair_merges_partial(monkeypatch):
    calls = {"n": 0, "users": []}

    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        calls["n"] += 1
        calls["users"].append(messages[-1]["content"])
        if calls["n"] == 1:
            # Keep [001], drop [002], and include an unexpected ID that should be ignored.
            return ChatResult(content="[001]\nOlá\n\n[999]\nextra\n", model=model, total_tokens=1)
        assert "missing subtitle blocks" in messages[-1]["content"].lower()
        assert "[002]" in messages[-1]["content"]
        assert "\n[001]\n" not in messages[-1]["content"]
        return ChatResult(content="[002]\nMundo\n", model=model, total_tokens=2)

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="m",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
    )
    assert outcome.document.blocks[0].text == "Olá"
    assert outcome.document.blocks[1].text == "Mundo"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_translation_shrinks_batch_after_failed_repairs(monkeypatch):
    sample4 = """1
00:00:01,000 --> 00:00:02,000
One

2
00:00:02,000 --> 00:00:03,000
Two

3
00:00:03,000 --> 00:00:04,000
Three

4
00:00:04,000 --> 00:00:05,000
Four
"""
    translations = {1: "Um", 2: "Dois", 3: "Três", 4: "Quatro"}
    source_text = {1: "One", 2: "Two", 3: "Three", 4: "Four"}
    calls = {"n": 0}

    def requested_ids(user: str) -> list[int]:
        ids = []
        for block_id, text in source_text.items():
            if f"[{block_id:03d}]\n{text}" in user:
                ids.append(block_id)
        return ids

    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        calls["n"] += 1
        ids = requested_ids(messages[-1]["content"])
        # Fail while the model is still asked for the original 4-block batch (or its repairs).
        if set(ids) == {1, 2, 3, 4} or set(ids) == {2, 3, 4}:
            return ChatResult(content="[001]\nUm\n", model=model, total_tokens=1)
        parts = [f"[{block_id:03d}]\n{translations[block_id]}" for block_id in ids]
        return ChatResult(content="\n\n".join(parts) + "\n", model=model, total_tokens=1)

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(sample4),
        model="m",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=4,
    )
    assert [b.text for b in outcome.document.blocks] == ["Um", "Dois", "Três", "Quatro"]
    # Initial + 2 targeted repairs on the large batch, then successful smaller calls.
    assert calls["n"] > 3


@pytest.mark.asyncio
async def test_translation_single_block_still_fails_hard(monkeypatch):
    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        return ChatResult(content="broken", model=model, total_tokens=1)

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    single = """1
00:00:01,000 --> 00:00:03,000
Hello
"""
    with pytest.raises(AIProviderError, match="validation"):
        await service.translate_document(
            parse_srt(single),
            model="m",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            batch_size=1,
        )


@pytest.mark.asyncio
async def test_translation_markup_mismatch_is_warning(monkeypatch):
    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        # Return translation without restoring italic tags (TAG0/TAG1 dropped).
        return ChatResult(content="[001]\nOlá\n", model=model, total_tokens=1)

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    source = """1
00:00:01,000 --> 00:00:02,000
<i>Hello</i>
"""
    outcome = await service.translate_document(
        parse_srt(source),
        model="m",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=1,
    )
    assert outcome.document.blocks[0].text == "Olá"
    assert outcome.warnings
    assert any("markup" in warning for warning in outcome.warnings)


@pytest.mark.asyncio
async def test_translation_retries_hard_block_individually(monkeypatch):
    calls = {"n": 0}

    async def fake_chat(*, model, messages, temperature=0, max_tokens=None):
        from app.translation.openrouter.client import ChatResult

        calls["n"] += 1
        user = messages[-1]["content"]
        # First batch returns only block 1; later single-block retries succeed.
        if "[002]" in user and "[001]" in user:
            return ChatResult(content="[001]\nOlá\n", model=model, total_tokens=1)
        if "[002]" in user:
            return ChatResult(content="[002]\nMundo\n", model=model, total_tokens=1)
        return ChatResult(content="[001]\nOlá\n", model=model, total_tokens=1)

    client = OpenRouterClient("key")
    monkeypatch.setattr(client, "chat_completion", fake_chat)
    service = OpenRouterTranslationService(client)
    outcome = await service.translate_document(
        parse_srt(SAMPLE),
        model="m",
        target_language_code="pt-PT",
        target_language_name="Portuguese (Portugal)",
        batch_size=50,
    )
    assert [b.text for b in outcome.document.blocks] == ["Olá", "Mundo"]
    assert calls["n"] > 1
