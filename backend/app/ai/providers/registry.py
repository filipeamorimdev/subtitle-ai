"""Provider registry — only OpenRouter is registered in production for alpha1."""

from __future__ import annotations

from app.ai.errors import AIProviderError
from app.ai.providers.base import AIProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> AIProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise AIProviderError(
                f"Unknown provider: {provider_id}",
                category="incompatible",
                is_retryable=False,
                provider_id=provider_id,
            )
        return provider

    def get_optional(self, provider_id: str) -> AIProvider | None:
        return self._providers.get(provider_id)

    def enabled(self) -> list[AIProvider]:
        return list(self._providers.values())

    def ids(self) -> list[str]:
        return list(self._providers.keys())

    def clear(self) -> None:
        self._providers.clear()


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_provider_registry() -> ProviderRegistry:
    """Test helper — replace the global registry."""
    global _registry
    _registry = ProviderRegistry()
    return _registry
