"""Bootstrap the production ProviderRegistry (OpenRouter only in alpha1)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.providers.registry import ProviderRegistry, get_provider_registry, reset_provider_registry


def bootstrap_providers(db: Session | None = None) -> ProviderRegistry:
    """Register concrete providers. MockAIProvider is never registered here."""
    registry = get_provider_registry()
    # Replace any prior OpenRouter instance so DB session stays current.
    if registry.get_optional("openrouter") is not None:
        # Keep registry; refresh with a DB-bound provider when session provided.
        if db is not None:
            registry.register(OpenRouterProvider(db))
        return registry
    registry.register(OpenRouterProvider(db))
    return registry


def bootstrap_providers_fresh(db: Session | None = None) -> ProviderRegistry:
    """Test helper — clear and re-register OpenRouter only."""
    registry = reset_provider_registry()
    registry.register(OpenRouterProvider(db))
    return registry
