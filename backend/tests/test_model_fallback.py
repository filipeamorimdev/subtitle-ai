"""Technical model fallback and validation-stay-put tests."""

from __future__ import annotations

import pytest

from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.ai.errors import AIProviderError
from app.translation.openrouter.client import ChatResult, OpenRouterError
from app.translation.openrouter.service import OpenRouterTranslationService, RetryableTranslationError


def _doc():
    blocks = [
        SubtitleBlock(index=1, start="00:00:01,000", end="00:00:02,000", text="Hello"),
        SubtitleBlock(index=2, start="00:00:03,000", end="00:00:04,000", text="World"),
        SubtitleBlock(index=3, start="00:00:05,000", end="00:00:06,000", text="Again"),
        SubtitleBlock(index=4, start="00:00:07,000", end="00:00:08,000", text="More"),
    ]
    return SubtitleDocument(format="srt", encoding="utf-8", blocks=blocks)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_completion(self, *, model, messages, **kwargs):
        self.calls.append(model)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(text: str, model="m"):
    return ChatResult(content=text, model=model, input_tokens=1, output_tokens=1, total_tokens=2)


def _batch(ids_texts):
    return "\n\n".join(f"[{i:03d}]\n{t}" for i, t in ids_texts) + "\n"


@pytest.mark.asyncio
async def test_timeout_falls_through_as_retryable():
    client = FakeClient(
        [
            OpenRouterError("OpenRouter request timed out", retryable=True),
        ]
    )
    svc = OpenRouterTranslationService(client)
    with pytest.raises(RetryableTranslationError):
        await svc.translate_document(_doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2)


@pytest.mark.asyncio
async def test_first_model_success_single_call_path():
    client = FakeClient(
        [
            _ok(_batch([(1, "Olá"), (2, "Mundo")])),
            _ok(_batch([(3, "De novo"), (4, "Mais")])),
        ]
    )
    svc = OpenRouterTranslationService(client)
    outcome = await svc.translate_document(
        _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
    )
    assert outcome.document.blocks[0].text == "Olá"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_batch_resume_keeps_successful_batches():
    client = FakeClient(
        [
            _ok(_batch([(1, "Olá"), (2, "Mundo")])),
            OpenRouterError("OpenRouter rate limited.", status_code=429, retryable=True),
        ]
    )
    svc = OpenRouterTranslationService(client)
    with pytest.raises(RetryableTranslationError) as exc:
        await svc.translate_document(
            _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
        )
    assert exc.value.checkpoint.completed_batches == 1
    assert 1 in exc.value.checkpoint.translated_by_id

    client2 = FakeClient([_ok(_batch([(3, "De novo"), (4, "Mais")]))])
    svc2 = OpenRouterTranslationService(client2)
    outcome = await svc2.translate_document(
        _doc(),
        model="b",
        target_language_code="pt-PT",
        target_language_name="Portuguese",
        batch_size=2,
        checkpoint=exc.value.checkpoint,
    )
    assert outcome.document.blocks[0].text == "Olá"
    assert outcome.document.blocks[2].text == "De novo"
    assert client2.calls == ["b"]


@pytest.mark.asyncio
async def test_validation_failure_is_not_retryable_pool_burn():
    client = FakeClient(
        [
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
            ChatResult(content="broken", model="a", total_tokens=1),
        ]
    )
    svc = OpenRouterTranslationService(client)
    with pytest.raises(AIProviderError) as exc:
        await svc.translate_document(
            _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
        )
    assert exc.value.retryable is False
    assert "validation" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_rate_limit_is_retryable():
    client = FakeClient([OpenRouterError("OpenRouter rate limited.", status_code=429, retryable=True)])
    svc = OpenRouterTranslationService(client)
    with pytest.raises(RetryableTranslationError):
        await svc.translate_document(
            _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
        )


@pytest.mark.asyncio
async def test_provider_5xx_is_retryable():
    client = FakeClient([OpenRouterError("OpenRouter request failed (503)", status_code=503, retryable=True)])
    svc = OpenRouterTranslationService(client)
    with pytest.raises(RetryableTranslationError):
        await svc.translate_document(
            _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
        )


@pytest.mark.asyncio
async def test_permanent_auth_error_does_not_fallback():
    client = FakeClient([OpenRouterError("OpenRouter authentication failed.", status_code=401)])
    svc = OpenRouterTranslationService(client)
    with pytest.raises(OpenRouterError) as exc:
        await svc.translate_document(
            _doc(), model="a", target_language_code="pt-PT", target_language_name="Portuguese", batch_size=2
        )
    assert not isinstance(exc.value, RetryableTranslationError)
    assert exc.value.retryable is False
