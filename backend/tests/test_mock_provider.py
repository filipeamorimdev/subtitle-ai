"""MockAIProvider tests — deterministic, never networks."""

from __future__ import annotations

import pytest

from app.ai.errors import AIProviderError, RateLimitError
from app.ai.models import ProviderStatus
from app.ai.providers.mock import MockAIProvider


@pytest.mark.asyncio
async def test_mock_provider_success_and_history():
    mock = MockAIProvider(default_content="translated")
    models = await mock.list_models()
    assert {m.model_id for m in models} == {"mock-free", "mock-paid"}
    result = await mock.chat_completion(
        model_id="mock-free",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "translated"
    assert result.provider_id == "mock"
    assert result.request_id
    assert mock.call_count >= 1
    assert mock.call_history[-1].method == "chat_completion"


@pytest.mark.asyncio
async def test_mock_provider_injected_errors():
    mock = MockAIProvider()
    mock.inject_rate_limit(on_call=1)
    with pytest.raises(RateLimitError):
        await mock.chat_completion(model_id="mock-free", messages=[{"role": "user", "content": "x"}])
    mock.clear_errors()
    mock2 = MockAIProvider()
    mock2.inject_timeout(on_call=1)
    with pytest.raises(AIProviderError) as exc:
        await mock2.chat_completion(model_id="mock-paid", messages=[{"role": "user", "content": "x"}])
    assert exc.value.category == "timeout"

    mock3 = MockAIProvider()
    mock3.inject_context_limit(on_call=1)
    with pytest.raises(AIProviderError) as exc3:
        await mock3.chat_completion(model_id="mock-free", messages=[{"role": "user", "content": "x"}])
    assert exc3.value.category == "context_overflow"

    mock4 = MockAIProvider()
    mock4.inject_unavailable(on_call=1)
    with pytest.raises(AIProviderError) as exc4:
        await mock4.chat_completion(model_id="mock-free", messages=[{"role": "user", "content": "x"}])
    assert exc4.value.category in {"provider_unavailable", "unavailable", "provider_error"}


@pytest.mark.asyncio
async def test_mock_health_cache():
    mock = MockAIProvider()
    first = await mock.test_connection()
    assert first.status == ProviderStatus.CONNECTED
    assert first.cached is False
    second = await mock.test_connection()
    assert second.cached is True
    mock.invalidate_health_cache()
    third = await mock.test_connection()
    assert third.cached is False


@pytest.mark.asyncio
async def test_mock_injected_latency():
    mock = MockAIProvider(latency_ms=5)
    result = await mock.chat_completion(
        model_id="mock-free",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
