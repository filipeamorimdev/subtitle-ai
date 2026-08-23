"""OpenRouter adapter normalization tests."""

from __future__ import annotations

from app.ai.errors import RateLimitError
from app.ai.models import (
    CAPABILITY_BATCH,
    CAPABILITY_FUNCTION_CALLING,
    CAPABILITY_TEXT_GENERATION,
    PricingTier,
    ToolCall,
    ToolSpec,
)
from app.ai.providers.openrouter import (
    PROVIDER_ID,
    normalize_chat_result,
    normalize_openrouter_model,
    openrouter_error_to_provider_error,
    tool_calls_from_chat_result,
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
    assert CAPABILITY_FUNCTION_CALLING not in model.capabilities


def test_openrouter_function_calling_capability():
    info = OpenRouterModelInfo(
        id="prov/tools-model",
        name="Tools",
        prompt_price_per_million=1.0,
        completion_price_per_million=2.0,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
        supported_parameters=["tools", "tool_choice", "temperature"],
    )
    model = normalize_openrouter_model(info)
    assert CAPABILITY_FUNCTION_CALLING in model.capabilities
    assert model.metadata.get("supported_parameters") == [
        "tools",
        "tool_choice",
        "temperature",
    ]


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
    assert response.tool_calls is None
    assert response.raw_metadata is None or "api_key" not in str(response.raw_metadata)
    assert response.raw_metadata is None or "authorization" not in str(response.raw_metadata).lower()

    mapped = openrouter_error_to_provider_error(
        OpenRouterError("rate limited", status_code=429, retryable=True, retry_after_seconds=2.5)
    )
    assert isinstance(mapped, RateLimitError)
    assert mapped.retry_after_seconds == 2.5


def test_tool_calls_from_chat_result():
    result = ChatResult(
        content="",
        model="m",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search_media",
                    "arguments": '{"query": "The Matrix"}',
                },
            }
        ],
    )
    calls = tool_calls_from_chat_result(result)
    assert calls is not None
    assert len(calls) == 1
    assert isinstance(calls[0], ToolCall)
    assert calls[0].id == "call_1"
    assert calls[0].name == "search_media"
    assert calls[0].arguments == {"query": "The Matrix"}

    response = normalize_chat_result(result, model_id="m", request_id="req-tools")
    assert response.tool_calls is not None
    assert response.tool_calls[0].name == "search_media"
    assert response.content == ""


def test_tool_spec_openai_shape():
    spec = ToolSpec(
        name="search_media",
        description="Search Bazarr media",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    payload = spec.to_openai_dict()
    assert payload["type"] == "function"
    assert payload["function"]["name"] == "search_media"
    assert payload["function"]["parameters"]["required"] == ["query"]
