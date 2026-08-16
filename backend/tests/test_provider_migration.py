"""Idempotent legacy OpenRouter → provider-aware migration tests."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.ai.credentials import ProviderAccountService
from app.ai.migration import migrate_legacy_openrouter
from app.core.secrets import encrypt_secret, load_or_create_fernet
from app.db import Base
from app.db.models import (
    AiModelPreferenceRow,
    AiProviderAccountRow,
    AiRoutingEventRow,
    AiUsageRecordRow,
    JobRow,
    OpenRouterModelPreferenceRow,
    SettingsRow,
    TranslationCacheRow,
)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_migrate_legacy_openrouter_idempotent(tmp_path):
    db = _session(tmp_path)
    fernet = load_or_create_fernet(tmp_path / "secret.key")
    ciphertext = encrypt_secret(fernet, "sk-test-key-1234567890")
    db.add(
        SettingsRow(
            id=1,
            openrouter_api_key_encrypted=ciphertext,
            openrouter_model="openai/gpt-4o-mini",
        )
    )
    db.add(
        OpenRouterModelPreferenceRow(
            model_id="openai/gpt-4o-mini",
            tier="paid",
            priority=1,
            enabled=True,
        )
    )
    db.add(
        AiUsageRecordRow(
            operation_type="translation",
            model_id="openai/gpt-4o-mini",
            status="success",
        )
    )
    db.add(AiRoutingEventRow(event="selected", model_id="openai/gpt-4o-mini"))
    db.add(
        JobRow(
            media_path="/m.mkv",
            source_subtitle_path="/s.srt",
            target_subtitle_path="/t.srt",
            model="openai/gpt-4o-mini",
            status="completed",
        )
    )
    db.add(
        TranslationCacheRow(
            source_hash="abc",
            target_language="pt-PT",
            model="openai/gpt-4o-mini",
            target_subtitle_path="/t.srt",
        )
    )
    db.commit()

    stats1 = migrate_legacy_openrouter(db)
    db.commit()
    stats2 = migrate_legacy_openrouter(db)
    db.commit()

    assert stats1["accounts_created"] == 1
    assert stats1["preferences_copied"] == 1
    assert stats2["accounts_created"] == 0
    assert stats2["preferences_copied"] == 0

    accounts = list(db.scalars(select(AiProviderAccountRow)).all())
    assert len(accounts) == 1
    assert accounts[0].provider_id == "openrouter"
    assert accounts[0].api_key_encrypted == ciphertext

    prefs = list(db.scalars(select(AiModelPreferenceRow)).all())
    assert len(prefs) == 1
    assert prefs[0].model_id == "openai/gpt-4o-mini"
    assert prefs[0].priority == 1

    usage = db.scalar(select(AiUsageRecordRow))
    assert usage.provider_id == "openrouter"
    assert db.get(JobRow, 1).provider_id == "openrouter"
    assert db.scalar(select(AiRoutingEventRow)).provider_id == "openrouter"
    cache = db.scalar(select(TranslationCacheRow))
    assert cache.provider_id == "openrouter"
    assert db.scalar(select(func.count()).select_from(AiProviderAccountRow)) == 1


def test_credential_legacy_fallback(tmp_path):
    db = _session(tmp_path)
    fernet = load_or_create_fernet(tmp_path / "secret.key")
    ciphertext = encrypt_secret(fernet, "sk-legacy-abcdef")
    db.add(
        SettingsRow(
            id=1,
            openrouter_api_key_encrypted=ciphertext,
            openrouter_model="m",
        )
    )
    db.commit()
    # No ai_provider_accounts row — should still resolve via legacy settings.
    service = ProviderAccountService(db, fernet)
    assert service.get_api_key("openrouter") == "sk-legacy-abcdef"
    public = service.get_public("openrouter")
    assert public.configured is True
    assert public.api_key_masked is not None
    assert "sk-legacy" not in (public.api_key_masked or "")
