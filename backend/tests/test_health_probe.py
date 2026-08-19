"""Live OpenRouter health probe uses routed translation models."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.ai.bootstrap import bootstrap_providers_fresh
from app.ai.models import ProviderStatus
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.providers.registry import reset_provider_registry
from app.api.schemas import SettingsUpdate
from app.core.config import get_app_config
from app.core.health import invalidate_ai_connection_health, probe_openrouter
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import (
    AiModelPreferenceRow,
    OpenRouterCatalogCacheRow,
    OpenRouterModelPreferenceRow,
)
from app.services.model_router import NO_ELIGIBLE_PING_MODEL
from app.services.settings import SettingsService
from app.translation.openrouter.client import OpenRouterClient, OpenRouterModelInfo


def _catalog_payload() -> list[dict]:
    payload = []
    for model_id, prompt, completion in [
        ("free/a", 0.0, 0.0),
        ("free/b", 0.0, 0.0),
        ("paid/a", 1.0, 2.0),
        ("openai/gpt-4o-mini", 0.15, 0.6),
    ]:
        payload.append(
            OpenRouterModelInfo(
                id=model_id,
                name=model_id,
                prompt_price_per_million=prompt,
                completion_price_per_million=completion,
                context_length=128000,
                input_modalities=["text"],
                output_modalities=["text"],
            ).to_dict()
        )
    return payload


def setup_probe_db(tmp_path, monkeypatch, prefs, *, strategy="free_first", allow_paid=False):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()
    reset_provider_registry()

    engine = create_engine(f"sqlite:///{config_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    fernet = load_or_create_fernet(config_dir / "secret.key")
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            openrouter_api_key="sk-test",
            routing_strategy=strategy,
            allow_paid_fallback=allow_paid,
        )
    )
    db.execute(delete(OpenRouterModelPreferenceRow))
    db.execute(delete(AiModelPreferenceRow))
    settings = SettingsService(db, fernet=fernet).get_or_create_row()
    settings.routing_strategy = strategy
    settings.allow_paid_fallback = allow_paid
    db.add(settings)
    db.add(OpenRouterCatalogCacheRow(id=1, payload_json=_catalog_payload(), stale=False))
    for i, (model_id, tier, enabled) in enumerate(prefs, start=1):
        db.add(
            AiModelPreferenceRow(
                provider_id="openrouter",
                model_id=model_id,
                tier=tier,
                priority=i,
                enabled=enabled,
            )
        )
        db.add(
            OpenRouterModelPreferenceRow(
                model_id=model_id, tier=tier, priority=i, enabled=enabled
            )
        )
    db.commit()
    bootstrap_providers_fresh(db)
    invalidate_ai_connection_health()
    return db


def _patch_ping(monkeypatch):
    pinged: list[str] = []

    async def fake_test(self, model: str):
        pinged.append(model)
        return {"ok": True, "model": model, "sample": "ok"}

    monkeypatch.setattr(OpenRouterClient, "test_connection", fake_test)
    return pinged


@pytest.mark.asyncio
async def test_probe_pings_first_free_only_model(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("free/a", "free", True), ("free/b", "free", True), ("paid/a", "paid", True)],
        strategy="free_only",
    )
    pinged = _patch_ping(monkeypatch)
    assert await probe_openrouter(db) == "ok"
    assert pinged == ["free/a"]


@pytest.mark.asyncio
async def test_probe_pings_first_paid_only_model(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("free/a", "free", True), ("paid/a", "paid", True), ("paid/b", "paid", True)],
        strategy="paid_only",
    )
    pinged = _patch_ping(monkeypatch)
    assert await probe_openrouter(db) == "ok"
    assert pinged == ["paid/a"]


@pytest.mark.asyncio
async def test_probe_free_first_does_not_ping_paid(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("free/a", "free", True), ("paid/a", "paid", True)],
        strategy="free_first",
        allow_paid=True,
    )
    pinged = _patch_ping(monkeypatch)
    assert await probe_openrouter(db) == "ok"
    assert pinged == ["free/a"]


@pytest.mark.asyncio
async def test_probe_empty_pool_is_unreachable_without_ping(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("paid/a", "paid", True)],
        strategy="free_only",
    )
    pinged = _patch_ping(monkeypatch)
    assert await probe_openrouter(db) == "unreachable"
    assert pinged == []


@pytest.mark.asyncio
async def test_probe_never_defaults_to_gpt4o_mini(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("free/b", "free", True)],
        strategy="free_only",
    )
    pinged = _patch_ping(monkeypatch)
    assert await probe_openrouter(db) == "ok"
    assert "openai/gpt-4o-mini" not in pinged
    assert pinged == ["free/b"]


@pytest.mark.asyncio
async def test_provider_test_without_model_uses_routing(tmp_path, monkeypatch):
    db = setup_probe_db(
        tmp_path,
        monkeypatch,
        [("free/a", "free", True), ("paid/a", "paid", True)],
        strategy="paid_only",
    )
    pinged = _patch_ping(monkeypatch)
    provider = OpenRouterProvider(db, api_key="sk-test")
    health = await provider.test_connection()
    assert health.status == ProviderStatus.CONNECTED
    assert health.model_id == "paid/a"
    assert pinged == ["paid/a"]


@pytest.mark.asyncio
async def test_provider_test_without_eligible_model_errors(tmp_path, monkeypatch):
    db = setup_probe_db(tmp_path, monkeypatch, [], strategy="free_only")
    pinged = _patch_ping(monkeypatch)
    provider = OpenRouterProvider(db, api_key="sk-test")
    health = await provider.test_connection()
    assert health.status == ProviderStatus.ERROR
    assert health.message == NO_ELIGIBLE_PING_MODEL
    assert pinged == []
