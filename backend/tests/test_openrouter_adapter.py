"""OpenRouter adapter normalization tests."""

from __future__ import annotations

from app.ai.errors import RateLimitError
from app.ai.models import CAPABILITY_BATCH, CAPABILITY_TEXT_GENERATION, PricingTier
from app.ai.providers.openrouter import (
    PROVIDER_ID,
    normalize_chat_result,
    normalize_openrouter_model,
    openrouter_error_to_provider_error,
)
from app.translation.openrouter.client import ChatResult, OpenRouterError, OpenRouterModelInfo


def test_openrouter_model_normalization():
    info = OpenRouterModelInfo(
        id="prov/model:free",
        name="Model",
        prompt_price_per_million=0.0,
        completion_price_per_million=0.0,
        context_length=8192,
        input_modalities=["text"],
        output_modalities=["text"],
    )
    model = normalize_openrouter_model(info)
    assert model.provider_id == PROVIDER_ID
    assert model.model_id == "prov/model:free"
    assert model.pricing_tier == PricingTier.FREE
    assert CAPABILITY_TEXT_GENERATION in model.capabilities


def test_openrouter_batch_capability():
    info = OpenRouterModelInfo(
        id="prov/model:batch",
        name="Batch",
        prompt_price_per_million=1.0,
        completion_price_per_million=2.0,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
    )
    model = normalize_openrouter_model(info)
    assert CAPABILITY_BATCH in model.capabilities
    assert model.pricing_tier == PricingTier.PAID


def test_openrouter_response_and_error_normalization():
    result = ChatResult(
        content="ok",
        model="m",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cost_usd=0.01,
        latency_ms=12,
    )
    response = normalize_chat_result(result, model_id="m", request_id="req-1")
    assert response.request_id == "req-1"
    assert response.input_tokens == 1
    assert response.output_tokens == 2
    assert response.latency_ms == 12
    assert float(response.actual_cost_usd) == 0.01
    assert response.raw_metadata is None or "api_key" not in str(response.raw_metadata)
    assert response.raw_metadata is None or "authorization" not in str(response.raw_metadata).lower()

    mapped = openrouter_error_to_provider_error(
        OpenRouterError("rate limited", status_code=429, retryable=True, retry_after_seconds=2.5)
    )
    assert isinstance(mapped, RateLimitError)
    assert mapped.retry_after_seconds == 2.5
