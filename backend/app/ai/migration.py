"""Idempotent migration of legacy OpenRouter data into provider-aware tables."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    AiModelCatalogCacheRow,
    AiModelPreferenceRow,
    AiProviderAccountRow,
    AiRoutingEventRow,
    AiUsageRecordRow,
    JobRow,
    OpenRouterCatalogCacheRow,
    OpenRouterModelPreferenceRow,
    SettingsRow,
    TranslationCacheRow,
)

logger = get_logger("ai.migration")

OPENROUTER_PROVIDER_ID = "openrouter"
OPENROUTER_DISPLAY_NAME = "OpenRouter"


def migrate_legacy_openrouter(db: Session) -> dict[str, int]:
    """
    Copy legacy OpenRouter credentials/preferences/history into generic tables.

    Idempotent: safe to run on every init_db(). Does not contact the network.
    Does not decrypt API keys — copies ciphertext only.
    """
    stats = {
        "accounts_created": 0,
        "preferences_copied": 0,
        "catalog_copied": 0,
        "usage_backfilled": 0,
        "routing_backfilled": 0,
        "jobs_backfilled": 0,
        "cache_backfilled": 0,
    }

    settings = db.get(SettingsRow, 1)
    existing_account = db.scalar(
        select(AiProviderAccountRow).where(
            AiProviderAccountRow.provider_id == OPENROUTER_PROVIDER_ID
        )
    )
    if existing_account is None and settings is not None and settings.openrouter_api_key_encrypted:
        db.add(
            AiProviderAccountRow(
                provider_id=OPENROUTER_PROVIDER_ID,
                display_name=OPENROUTER_DISPLAY_NAME,
                enabled=True,
                api_key_encrypted=settings.openrouter_api_key_encrypted,
                base_url=None,
            )
        )
        stats["accounts_created"] = 1
        logger.info("Migrated legacy OpenRouter API key into ai_provider_accounts")
    elif existing_account is None:
        # Ensure a row exists even without a key so the Providers UI can manage it.
        db.add(
            AiProviderAccountRow(
                provider_id=OPENROUTER_PROVIDER_ID,
                display_name=OPENROUTER_DISPLAY_NAME,
                enabled=True,
                api_key_encrypted=None,
                base_url=None,
            )
        )
        stats["accounts_created"] = 1

    generic_pref_count = db.scalar(select(func.count()).select_from(AiModelPreferenceRow)) or 0
    if generic_pref_count == 0:
        db.flush()
        legacy_prefs = list(db.scalars(select(OpenRouterModelPreferenceRow)).all())
        if not legacy_prefs:
            # Seed legacy openrouter_model_preferences first (v0.1 → v0.2 path).
            from app.services.model_preferences import seed_legacy_model_preference

            seed_legacy_model_preference(db)
            db.flush()
            legacy_prefs = list(db.scalars(select(OpenRouterModelPreferenceRow)).all())
        for pref in legacy_prefs:
            exists = db.scalar(
                select(AiModelPreferenceRow).where(
                    AiModelPreferenceRow.provider_id == OPENROUTER_PROVIDER_ID,
                    AiModelPreferenceRow.model_id == pref.model_id,
                )
            )
            if exists is not None:
                continue
            db.add(
                AiModelPreferenceRow(
                    provider_id=OPENROUTER_PROVIDER_ID,
                    model_id=pref.model_id,
                    tier=pref.tier,
                    priority=pref.priority,
                    enabled=pref.enabled,
                )
            )
            stats["preferences_copied"] += 1
        if stats["preferences_copied"]:
            logger.info(
                "Migrated %s OpenRouter model preferences into ai_model_preferences",
                stats["preferences_copied"],
            )

    generic_catalog = db.get(AiModelCatalogCacheRow, OPENROUTER_PROVIDER_ID)
    if generic_catalog is None:
        legacy_catalog = db.get(OpenRouterCatalogCacheRow, 1)
        if legacy_catalog is not None and legacy_catalog.payload_json is not None:
            db.add(
                AiModelCatalogCacheRow(
                    provider_id=OPENROUTER_PROVIDER_ID,
                    payload_json=legacy_catalog.payload_json,
                    fetched_at=legacy_catalog.fetched_at,
                    stale=bool(legacy_catalog.stale),
                )
            )
            stats["catalog_copied"] = 1
            logger.info("Migrated OpenRouter catalog cache into ai_model_catalog_cache")

    # Backfill provider_id on historical rows (NULL or missing default).
    # SQLite with create_all may have added columns as nullable without default
    # for existing rows — use raw SQL for robustness.
    stats["usage_backfilled"] = _backfill_provider_id(db, "ai_usage_records")
    stats["routing_backfilled"] = _backfill_provider_id(db, "ai_routing_events")
    stats["jobs_backfilled"] = _backfill_provider_id(db, "jobs")
    stats["cache_backfilled"] = _backfill_provider_id(db, "translation_cache")

    # Also set via ORM for in-memory consistency when rows were loaded before ALTER.
    for row in db.scalars(
        select(AiUsageRecordRow).where(
            (AiUsageRecordRow.provider_id.is_(None)) | (AiUsageRecordRow.provider_id == "")
        )
    ).all():
        row.provider_id = OPENROUTER_PROVIDER_ID
        db.add(row)
    for row in db.scalars(
        select(AiRoutingEventRow).where(AiRoutingEventRow.provider_id.is_(None))
    ).all():
        row.provider_id = OPENROUTER_PROVIDER_ID
        db.add(row)
    for row in db.scalars(
        select(JobRow).where((JobRow.provider_id.is_(None)) | (JobRow.provider_id == ""))
    ).all():
        row.provider_id = OPENROUTER_PROVIDER_ID
        db.add(row)
    for row in db.scalars(
        select(TranslationCacheRow).where(
            (TranslationCacheRow.provider_id.is_(None)) | (TranslationCacheRow.provider_id == "")
        )
    ).all():
        row.provider_id = OPENROUTER_PROVIDER_ID
        db.add(row)

    db.flush()
    return stats


def _backfill_provider_id(db: Session, table: str) -> int:
    """Set provider_id='openrouter' where NULL/empty. Returns rows affected."""
    try:
        result = db.execute(
            text(
                f"UPDATE {table} SET provider_id = :pid "
                "WHERE provider_id IS NULL OR provider_id = ''"
            ),
            {"pid": OPENROUTER_PROVIDER_ID},
        )
        return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        # Table/column may not exist yet during early create_all paths.
        return 0
