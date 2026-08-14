"""Model router policy tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import OpenRouterCatalogCacheRow, OpenRouterModelPreferenceRow, SettingsRow
from app.services.model_router import ModelRouter, RoutingPolicy
from app.translation.openrouter.client import OpenRouterModelInfo


def setup_db(tmp_path, prefs, strategy="free_first", allow_paid=False, allow_free=True, allow_unknown=False, cap=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    db.add(
        SettingsRow(
            id=1,
            openrouter_model="x",
            routing_strategy=strategy,
            allow_paid_fallback=allow_paid,
            allow_free_fallback=allow_free,
            allow_unknown_pricing=allow_unknown,
            maximum_cost_per_job_micro_usd=cap,
            monthly_budget_enabled=False,
        )
    )
    payload = []
    for model_id, prompt, completion, ctx, inputs, outputs in [
        ("free/a", 0.0, 0.0, 128000, ["text"], ["text"]),
        ("free/b", 0.0, 0.0, 128000, ["text"], ["text"]),
        ("paid/a", 1.0, 2.0, 128000, ["text"], ["text"]),
        ("paid/b", 5.0, 10.0, 128000, ["text"], ["text"]),
        ("img/x", 0.0, 0.0, 128000, ["image"], ["image"]),
        ("unk/x", None, None, 128000, ["text"], ["text"]),
    ]:
        info = OpenRouterModelInfo(
            id=model_id,
            name=model_id,
            prompt_price_per_million=prompt,
            completion_price_per_million=completion,
            context_length=ctx,
            input_modalities=inputs,
            output_modalities=outputs,
        )
        payload.append(info.to_dict())
    db.add(OpenRouterCatalogCacheRow(id=1, payload_json=payload, stale=False))
    for i, (model_id, tier, enabled) in enumerate(prefs, start=1):
        db.add(OpenRouterModelPreferenceRow(model_id=model_id, tier=tier, priority=i, enabled=enabled))
    db.commit()
    return db


def ids(result):
    return [c.model_id for c in result.candidates]


def test_free_only(tmp_path):
    db = setup_db(tmp_path, [("free/a", "free", True), ("free/b", "free", True), ("paid/a", "paid", True)], strategy="free_only")
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="free_only"))
    assert ids(result) == ["free/a", "free/b"]


def test_paid_only(tmp_path):
    db = setup_db(tmp_path, [("free/a", "free", True), ("paid/a", "paid", True), ("paid/b", "paid", True)], strategy="paid_only")
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="paid_only"))
    assert ids(result) == ["paid/a", "paid/b"]


def test_free_first_without_paid_fallback(tmp_path):
    db = setup_db(tmp_path, [("free/a", "free", True), ("paid/a", "paid", True)])
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="free_first", allow_paid_fallback=False))
    assert ids(result) == ["free/a"]


def test_free_first_with_paid_fallback(tmp_path):
    db = setup_db(tmp_path, [("free/a", "free", True), ("paid/a", "paid", True)])
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="free_first", allow_paid_fallback=True))
    assert ids(result) == ["free/a", "paid/a"]


def test_paid_first_with_free_fallback(tmp_path):
    db = setup_db(tmp_path, [("free/a", "free", True), ("paid/a", "paid", True)])
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="paid_first", allow_free_fallback=True))
    assert ids(result) == ["paid/a", "free/a"]


def test_priority_disabled_duplicate(tmp_path):
    db = setup_db(tmp_path, [("free/b", "free", True), ("free/a", "free", False)])
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="free_only"))
    assert ids(result) == ["free/b"]


def test_incompatible_and_unknown_blocked(tmp_path):
    db = setup_db(tmp_path, [("img/x", "free", True), ("unk/x", "paid", True)], strategy="free_first", allow_paid=True)
    result = ModelRouter(db).select_models(
        policy=RoutingPolicy(strategy="free_first", allow_paid_fallback=True, allow_unknown_pricing=False)
    )
    assert ids(result) == []


def test_unknown_allowed(tmp_path):
    db = setup_db(tmp_path, [("unk/x", "paid", True)], strategy="paid_only", allow_unknown=True)
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="paid_only", allow_unknown_pricing=True))
    assert ids(result) == ["unk/x"]


def test_per_job_budget_skips_expensive(tmp_path):
    db = setup_db(tmp_path, [("paid/a", "paid", True), ("paid/b", "paid", True)], strategy="paid_only", cap=1000)
    result = ModelRouter(db).select_models(
        policy=RoutingPolicy(strategy="paid_only", maximum_cost_per_job_micro_usd=1000),
        estimated_input_tokens=1_000_000,
        estimated_output_tokens=1_000_000,
    )
    # paid/a = $3, paid/b = $15; cap is $0.001 so both skipped
    assert result.blocked_reason == "blocked_by_cost_policy"
    assert ids(result) == []


def test_catalog_miss_keeps_configured_paid_model(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    db.add(
        SettingsRow(
            id=1,
            openrouter_model="openai/gpt-4o-mini",
            routing_strategy="paid_only",
            allow_paid_fallback=False,
            allow_unknown_pricing=False,
        )
    )
    db.add(OpenRouterModelPreferenceRow(model_id="openai/gpt-4o-mini", tier="paid", priority=1, enabled=True))
    db.commit()
    result = ModelRouter(db).select_models(policy=RoutingPolicy(strategy="paid_only"))
    assert ids(result) == ["openai/gpt-4o-mini"]
