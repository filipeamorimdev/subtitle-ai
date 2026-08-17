"""Mock AI provider for unit/integration tests — never contacts a real network."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.ai.errors import (
    AIProviderError,
    ContextLimitError,
    InvalidRequestError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.ai.models import (
    CAPABILITY_TEXT_GENERATION,
    AIModel,
    AIResponse,
    Message,
    PricingTier,
    ProviderHealth,
    ProviderStatus,
)
from app.ai.providers.base import AIProvider

PROVIDER_ID = "mock"


@dataclass
class MockCallRecord:
    method: str
    model_id: str | None
    messages: list[dict[str, str]] | None
    request_id: str | None
    kwargs: dict[str, Any] = field(default_factory=dict)


class MockAIProvider(AIProvider):
    """Deterministic test provider with injectable latency/errors."""

    provider_id = PROVIDER_ID
    display_name = "Mock Provider"

    def __init__(
        self,
        *,
        configured: bool = True,
        default_content: str = "ok",
        latency_ms: int = 0,
        global_error: Exception | None = None,
        response_factory: Callable[..., AIResponse] | None = None,
        models: list[AIModel] | None = None,
    ) -> None:
        self._configured = configured
        self.default_content = default_content
        self.latency_ms = latency_ms
        self.global_error = global_error
        self.response_factory = response_factory
        self._call_errors: dict[int, Exception] = {}
        self.call_history: list[MockCallRecord] = []
        self.call_count = 0
        self._health_cache: ProviderHealth | None = None
        self._health_cached = False
        self.models = models or [
            AIModel(
                provider_id=PROVIDER_ID,
                model_id="mock-free",
                name="Mock Free",
                context_length=128_000,
                input_price_per_million=Decimal("0"),
                output_price_per_million=Decimal("0"),
                capabilities={CAPABILITY_TEXT_GENERATION},
                pricing_tier=PricingTier.FREE,
            ),
            AIModel(
                provider_id=PROVIDER_ID,
                model_id="mock-paid",
                name="Mock Paid",
                context_length=128_000,
                input_price_per_million=Decimal("1.0"),
                output_price_per_million=Decimal("2.0"),
                capabilities={CAPABILITY_TEXT_GENERATION},
                pricing_tier=PricingTier.PAID,
            ),
        ]

    def is_configured(self) -> bool:
        return self._configured

    def supports(self, capability: str) -> bool:
        return capability == CAPABILITY_TEXT_GENERATION

    def set_configured(self, value: bool) -> None:
        self._configured = value
        self.invalidate_health_cache()

    def inject_error(self, error: Exception, *, on_call: int | None = None) -> None:
        """Inject a global error, or an error on a specific 1-based call number."""
        if on_call is None:
            self.global_error = error
        else:
            self._call_errors[on_call] = error

    def inject_rate_limit(self, *, on_call: int | None = None, retry_after: float = 1.0) -> None:
        self.inject_error(
            RateLimitError("mock rate limit", retry_after_seconds=retry_after, provider_id=PROVIDER_ID),
            on_call=on_call,
        )

    def inject_timeout(self, *, on_call: int | None = None) -> None:
        self.inject_error(
            ProviderUnavailableError("mock timeout", category="timeout", provider_id=PROVIDER_ID),
            on_call=on_call,
        )

    def inject_context_limit(self, *, on_call: int | None = None) -> None:
        self.inject_error(
            ContextLimitError("mock context limit", provider_id=PROVIDER_ID),
            on_call=on_call,
        )

    def inject_unavailable(self, *, on_call: int | None = None) -> None:
        self.inject_error(
            ProviderUnavailableError("mock unavailable", provider_id=PROVIDER_ID),
            on_call=on_call,
        )

    def inject_invalid(self, *, on_call: int | None = None) -> None:
        self.inject_error(
            InvalidRequestError("mock invalid response", provider_id=PROVIDER_ID),
            on_call=on_call,
        )

    def clear_errors(self) -> None:
        self.global_error = None
        self._call_errors.clear()

    def invalidate_health_cache(self) -> None:
        self._health_cache = None
        self._health_cached = False

    def _maybe_raise(self) -> None:
        self.call_count += 1
        if self.call_count in self._call_errors:
            raise self._call_errors[self.call_count]
        if self.global_error is not None:
            raise self.global_error

    def _normalize_messages(
        self, messages: list[Message] | list[dict[str, str]]
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, Message):
                out.append({"role": msg.role, "content": msg.content})
            else:
                out.append({"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))})
        return out

    async def test_connection(self, model_id: str | None = None) -> ProviderHealth:
        if self._health_cached and self._health_cache is not None:
            cached = self._health_cache
            return ProviderHealth(
                status=cached.status,
                provider_id=cached.provider_id,
                message=cached.message,
                configured=cached.configured,
                cached=True,
                tested_at=cached.tested_at,
                model_id=cached.model_id,
            )
        if not self.is_configured():
            health = ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                provider_id=PROVIDER_ID,
                message="Mock provider not configured",
                configured=False,
                cached=False,
                tested_at=datetime.now(timezone.utc),
                model_id=model_id,
            )
            return health
        try:
            self._maybe_raise()
            # Undo the call_count bump for connection tests that shouldn't
            # consume injected per-call errors intended for chat_completion —
            # actually keep it so injected errors work for test_connection too.
            health = ProviderHealth(
                status=ProviderStatus.CONNECTED,
                provider_id=PROVIDER_ID,
                message="Mock connected",
                configured=True,
                cached=False,
                tested_at=datetime.now(timezone.utc),
                model_id=model_id or "mock-free",
            )
        except AIProviderError as exc:
            health = ProviderHealth(
                status=ProviderStatus.ERROR,
                provider_id=PROVIDER_ID,
                message=str(exc),
                configured=True,
                cached=False,
                tested_at=datetime.now(timezone.utc),
                model_id=model_id,
            )
        self._health_cache = health
        self._health_cached = True
        return health

    async def list_models(self) -> list[AIModel]:
        self.call_history.append(
            MockCallRecord(method="list_models", model_id=None, messages=None, request_id=None)
        )
        self._maybe_raise()
        return list(self.models)

    async def chat_completion(
        self,
        *,
        model_id: str,
        messages: list[Message] | list[dict[str, str]],
        temperature: float = 0,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> AIResponse:
        req_id = request_id or str(uuid.uuid4())
        normalized = self._normalize_messages(messages)
        self.call_history.append(
            MockCallRecord(
                method="chat_completion",
                model_id=model_id,
                messages=normalized,
                request_id=req_id,
                kwargs={"temperature": temperature, "max_tokens": max_tokens},
            )
        )
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        self._maybe_raise()
        if self.response_factory is not None:
            return self.response_factory(
                model_id=model_id,
                messages=normalized,
                request_id=req_id,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return AIResponse(
            provider_id=PROVIDER_ID,
            model_id=model_id,
            content=self.default_content,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            actual_cost_usd=Decimal("0") if model_id == "mock-free" else Decimal("0.001"),
            latency_ms=self.latency_ms,
            request_id=req_id,
            raw_metadata={"request_id": req_id, "finish_reason": "stop"},
        )
