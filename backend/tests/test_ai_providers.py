"""Provider registry, capabilities, and bootstrap tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.bootstrap import bootstrap_providers_fresh
from app.ai.errors import AIProviderError
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.registry import ProviderRegistry, reset_provider_registry
from app.db import Base


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_registry_register_get_unknown():
    registry = ProviderRegistry()
    mock = MockAIProvider()
    registry.register(mock)
    assert registry.get("mock") is mock
    assert [p.provider_id for p in registry.enabled()] == ["mock"]
    with pytest.raises(AIProviderError, match="Unknown provider"):
        registry.get("nope")


def test_bootstrap_registers_openrouter_only(tmp_path):
    db = _session(tmp_path)
    reset_provider_registry()
    registry = bootstrap_providers_fresh(db)
    assert registry.ids() == ["openrouter"]
    assert registry.get_optional("mock") is None
    provider = registry.get("openrouter")
    assert provider.supports("text_generation")


def test_user_messages_for_provider_errors():
    from app.ai.errors import (
        AuthenticationError,
        ContextLimitError,
        InvalidRequestError,
        ModelNotFoundError,
        ProviderUnavailableError,
        RateLimitError,
        user_message_for_provider_error,
    )

    assert "rate-limiting" in user_message_for_provider_error(RateLimitError()).lower()
    assert "authentication" in user_message_for_provider_error(AuthenticationError()).lower()
    assert "too large" in user_message_for_provider_error(ContextLimitError()).lower()
    assert "unavailable" in user_message_for_provider_error(ProviderUnavailableError()).lower()
    assert "model" in user_message_for_provider_error(ModelNotFoundError()).lower()
    assert "rejected" in user_message_for_provider_error(InvalidRequestError()).lower()
    validation = AIProviderError("bad output", category="validation_error")
    assert "quality" in user_message_for_provider_error(validation).lower()

