"""Legacy openrouter_model migration tests."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import OpenRouterModelPreferenceRow, SettingsRow
from app.services.model_preferences import seed_legacy_model_preference


def test_legacy_paid_model_becomes_paid_only(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(
        SettingsRow(
            id=1,
            openrouter_model="openai/gpt-4o-mini",
            routing_strategy="free_first",
            allow_paid_fallback=False,
        )
    )
    db.commit()
    seed_legacy_model_preference(db)
    db.commit()
    prefs = list(db.scalars(select(OpenRouterModelPreferenceRow)).all())
    assert len(prefs) == 1
    assert prefs[0].model_id == "openai/gpt-4o-mini"
    assert prefs[0].tier == "paid"
    assert prefs[0].priority == 1
    settings = db.get(SettingsRow, 1)
    assert settings.routing_strategy == "paid_only"
    assert settings.allow_paid_fallback is False


def test_legacy_free_suffix(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="meta-llama/llama-3.1-8b-instruct:free"))
    db.commit()
    seed_legacy_model_preference(db)
    db.commit()
    pref = db.scalars(select(OpenRouterModelPreferenceRow)).first()
    assert pref.tier == "free"
    settings = db.get(SettingsRow, 1)
    assert settings.routing_strategy == "free_only"
    assert settings.allow_paid_fallback is False


def test_seed_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SettingsRow(id=1, openrouter_model="openai/gpt-4o-mini"))
    db.commit()
    seed_legacy_model_preference(db)
    db.commit()
    assert seed_legacy_model_preference(db) is None
    assert db.scalar(select(OpenRouterModelPreferenceRow)) is not None
