"""Generic AI provider protocol / base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.ai.models import (
    CAPABILITY_TEXT_GENERATION,
    AIModel,
    AIResponse,
    Message,
    ProviderHealth,
    ToolSpec,
)


class AIProvider(ABC):
    """Provider adapters own HTTP, auth, endpoints, and catalog specifics."""

    provider_id: str
    display_name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Fast local check — must not contact the network."""

    def supports(self, capability: str) -> bool:
        """Provider-level capability (models may further restrict)."""
        return capability == CAPABILITY_TEXT_GENERATION

    @abstractmethod
    async def test_connection(self, model_id: str | None = None) -> ProviderHealth:
        ...

    @abstractmethod
    async def list_models(self) -> list[AIModel]:
        ...

    @abstractmethod
    async def chat_completion(
        self,
        *,
        messages: list[Message] | list[dict[str, Any]],
        model_id: str,
        temperature: float = 0,
        max_tokens: int | None = None,
        request_id: str | None = None,
        tools: list[ToolSpec] | list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AIResponse:
        ...

    async def run_chat_batch(
        self,
        *,
        model_id: str,
        requests: list[Any],
        **kwargs: Any,
    ) -> dict[str, AIResponse]:
        """Optional provider-specific batch API. Default: not supported."""
        raise NotImplementedError(
            f"Provider {self.provider_id} does not support batch completions"
        )

    def invalidate_health_cache(self) -> None:
        """Clear cached connection-test results (e.g. after credential rotation)."""
        return None
