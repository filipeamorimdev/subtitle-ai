"""Model catalog cache, classification, and compatibility tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import OpenRouterCatalogCacheRow
from app.services.model_catalog import (
    ModelCatalogService,
    check_compatibility,
    classify_pricing_tier,
)
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError, OpenRouterModelInfo


def _patch_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _info(**kwargs) -> OpenRouterModelInfo:
    defaults = dict(
        id="prov/model",
        name="Model",
        prompt_price_per_million=0.0,
        completion_price_per_million=0.0,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
    )
    defaults.update(kwargs)
    return OpenRouterModelInfo(**defaults)


def test_classify_free_paid_unknown():
    assert classify_pricing_tier(0.0, 0.0) == "free"
    assert classify_pricing_tier(0.15, 0.6) == "paid"
    assert classify_pricing_tier(None, 0.0) == "unknown"
    assert classify_pricing_tier(0.0, None) == "unknown"
    assert _info().pricing_tier == "free"
    assert _info(prompt_price_per_million=None, completion_price_per_million=None).pricing_tier == "unknown"


def test_compatibility_text_and_context():
    ok, reason = check_compatibility(_info(), batch_size=25)
    assert ok
    assert "Compatible" in reason
    bad, reason = check_compatibility(_info(output_modalities=["image"]), batch_size=25)
    assert not bad
    assert reason == "Not compatible with Subtitle AI translation"
    tiny, reason = check_compatibility(_info(context_length=100), batch_size=25)
    assert not tiny


@pytest.mark.asyncio
async def test_catalog_refresh_and_stale(tmp_path, monkeypatch):
    db = _session(tmp_path)

    async def fake_list(**kwargs):
        return [_info(id="free/a", name="A")]

    monkeypatch.setattr(OpenRouterClient, "list_models", staticmethod(fake_list))
    svc = ModelCatalogService(db)
    snap = await svc.get_models(force_refresh=True, api_key="test")
    assert len(snap.models) == 1
    assert snap.stale is False
    cached = svc.get_cached()
    assert cached and cached.models[0].model_id == "free/a"

    async def fail(**kwargs):
        raise OpenRouterError("unavailable")

    monkeypatch.setattr(OpenRouterClient, "list_models", staticmethod(fail))
    snap2 = await svc.get_models(force_refresh=True, api_key="test")
    assert snap2.stale is True
    assert snap2.models[0].model_id == "free/a"


@pytest.mark.asyncio
async def test_catalog_malformed_and_missing_pricing(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "x", "pricing": {}}, {"nope": True}]})

    _patch_httpx(monkeypatch, handler)
    models = await OpenRouterClient.list_models()
    assert models[0].id == "x"
    assert models[0].prompt_price_per_million is None
    assert models[0].pricing_tier == "unknown"


@pytest.mark.asyncio
async def test_catalog_unavailable_api(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    _patch_httpx(monkeypatch, handler)
    with pytest.raises(OpenRouterError):
        await OpenRouterClient.list_models()


def test_catalog_ttl_expires(tmp_path):
    db = _session(tmp_path)
    db.add(
        OpenRouterCatalogCacheRow(
            id=1,
            payload_json=[_info(id="free/a").to_dict()],
            stale=False,
            fetched_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
    )
    db.commit()
    svc = ModelCatalogService(db)
    assert svc.is_fresh() is False
    fresh = OpenRouterCatalogCacheRow(
        id=1,
        payload_json=[_info(id="free/a").to_dict()],
        stale=False,
        fetched_at=datetime.now(timezone.utc),
    )
    db.merge(fresh)
    db.commit()
    assert svc.is_fresh() is True
