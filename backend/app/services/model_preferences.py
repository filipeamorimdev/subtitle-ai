"""Provider-aware model preference pools with legacy OpenRouter fallback."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.core.logging import get_logger
from app.db.models import (
    AiModelPreferenceRow,
    OpenRouterModelPreferenceRow,
    SettingsRow,
)
from app.translation.openrouter.client import batch_base_model

logger = get_logger("model_preferences")


def _classify_tier_from_cache(db: Session, model_id: str, *, provider_id: str = OPENROUTER_PROVIDER_ID) -> str:
    """Best-effort tier from catalog cache; unknown if unavailable."""
    try:
        from app.services.model_catalog import ModelCatalogService

        catalog = ModelCatalogService(db)
        info = catalog.get_model(provider_id, model_id)
        if info is None:
            return "unknown"
        return info.pricing_tier.value if hasattr(info.pricing_tier, "value") else str(info.pricing_tier)
    except Exception:  # noqa: BLE001
        return "unknown"


def seed_legacy_model_preference(db: Session) -> OpenRouterModelPreferenceRow | None:
    """
    If no legacy preferences exist, seed from settings.openrouter_model.

    Preserves v0.1 single-model behavior:
    - free → routing_strategy=free_only
    - paid/unknown → routing_strategy=paid_only
    - never enables allow_paid_fallback
    """
    count = db.scalar(select(func.count()).select_from(OpenRouterModelPreferenceRow)) or 0
    if count > 0:
        return None

    settings = db.get(SettingsRow, 1)
    if settings is None:
        return None

    model_id = (settings.openrouter_model or "").strip()
    if not model_id:
        return None

    base_id = batch_base_model(model_id)
    pref_model_id = model_id
    tier = _classify_tier_from_cache(db, base_id)
    if tier == "unknown":
        lowered = base_id.lower()
        if ":free" in lowered or lowered.endswith("/free"):
            tier = "free"
        else:
            tier = "paid"

    row = OpenRouterModelPreferenceRow(
        model_id=pref_model_id,
        tier="free" if tier == "free" else "paid",
        priority=1,
        enabled=True,
    )
    db.add(row)

    if tier == "free":
        settings.routing_strategy = "free_only"
    else:
        settings.routing_strategy = "paid_only"
    settings.allow_paid_fallback = False

    db.add(settings)
    logger.info(
        "Seeded legacy openrouter_model=%s as tier=%s strategy=%s",
        pref_model_id,
        row.tier,
        settings.routing_strategy,
    )
    return row


def _mirror_to_legacy(db: Session, row: AiModelPreferenceRow) -> None:
    """Keep openrouter_model_preferences in sync for OpenRouter during alpha1."""
    if row.provider_id != OPENROUTER_PROVIDER_ID:
        return
    legacy = db.scalar(
        select(OpenRouterModelPreferenceRow).where(
            OpenRouterModelPreferenceRow.model_id == row.model_id
        )
    )
    if legacy is None:
        legacy = OpenRouterModelPreferenceRow(
            model_id=row.model_id,
            tier=row.tier,
            priority=row.priority,
            enabled=row.enabled,
        )
    else:
        legacy.tier = row.tier
        legacy.priority = row.priority
        legacy.enabled = row.enabled
    db.add(legacy)


def _delete_legacy(db: Session, model_id: str, provider_id: str) -> None:
    if provider_id != OPENROUTER_PROVIDER_ID:
        return
    legacy = db.scalar(
        select(OpenRouterModelPreferenceRow).where(
            OpenRouterModelPreferenceRow.model_id == model_id
        )
    )
    if legacy is not None:
        db.delete(legacy)


def list_preferences(
    db: Session,
    *,
    tier: str | None = None,
    enabled_only: bool = False,
    provider_id: str | None = None,
) -> list[AiModelPreferenceRow]:
    """Prefer generic preferences; fall back to legacy OpenRouter rows if empty."""
    query = select(AiModelPreferenceRow)
    if provider_id:
        query = query.where(AiModelPreferenceRow.provider_id == provider_id)
    if tier:
        query = query.where(AiModelPreferenceRow.tier == tier)
    if enabled_only:
        query = query.where(AiModelPreferenceRow.enabled.is_(True))
    query = query.order_by(
        AiModelPreferenceRow.provider_id.asc(),
        AiModelPreferenceRow.tier.asc(),
        AiModelPreferenceRow.priority.asc(),
        AiModelPreferenceRow.id.asc(),
    )
    rows = list(db.scalars(query).all())
    if rows:
        return rows

    # Compatibility fallback: legacy OpenRouter preferences not yet migrated.
    legacy_query = select(OpenRouterModelPreferenceRow)
    if tier:
        legacy_query = legacy_query.where(OpenRouterModelPreferenceRow.tier == tier)
    if enabled_only:
        legacy_query = legacy_query.where(OpenRouterModelPreferenceRow.enabled.is_(True))
    legacy_query = legacy_query.order_by(
        OpenRouterModelPreferenceRow.tier.asc(),
        OpenRouterModelPreferenceRow.priority.asc(),
        OpenRouterModelPreferenceRow.id.asc(),
    )
    legacy_rows = list(db.scalars(legacy_query).all())
    if not legacy_rows:
        return []
    # Synthesize AiModelPreferenceRow objects (not persisted) for router use.
    # Prefer migrating so subsequent calls hit the generic table.
    from app.ai.migration import migrate_legacy_openrouter

    migrate_legacy_openrouter(db)
    db.flush()
    return list(db.scalars(query).all())


def sync_legacy_openrouter_model(db: Session) -> None:
    """Keep settings.openrouter_model aligned with the first enabled preference."""
    settings = db.get(SettingsRow, 1)
    if settings is None:
        return
    prefs = list_preferences(db, enabled_only=True, provider_id=OPENROUTER_PROVIDER_ID)
    if not prefs:
        return
    free = [p for p in prefs if p.tier == "free"]
    paid = [p for p in prefs if p.tier == "paid"]
    primary = free[0] if free else paid[0]
    if settings.openrouter_model != primary.model_id:
        settings.openrouter_model = primary.model_id
        db.add(settings)


class ModelPreferenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_seeded(self) -> None:
        seed_legacy_model_preference(self.db)
        from app.ai.migration import migrate_legacy_openrouter

        migrate_legacy_openrouter(self.db)
        self.db.flush()

    def list_all(
        self,
        *,
        enabled_only: bool = False,
        provider_id: str | None = None,
    ) -> list[AiModelPreferenceRow]:
        self.ensure_seeded()
        return list_preferences(self.db, enabled_only=enabled_only, provider_id=provider_id)

    def add(
        self,
        *,
        model_id: str,
        tier: str,
        enabled: bool = True,
        provider_id: str = OPENROUTER_PROVIDER_ID,
    ) -> AiModelPreferenceRow:
        model_id = model_id.strip()
        if tier not in ("free", "paid"):
            raise ValueError("tier must be free or paid")
        catalog_tier = _classify_tier_from_cache(
            self.db, batch_base_model(model_id), provider_id=provider_id
        )
        if catalog_tier == "paid" and tier == "free":
            raise ValueError(
                f"Model {model_id} is priced as paid in the catalog and cannot be added to the free pool"
            )
        if catalog_tier == "free" and tier == "paid":
            raise ValueError(
                f"Model {model_id} is priced as free in the catalog and cannot be added to the paid pool"
            )
        existing = self.db.scalar(
            select(AiModelPreferenceRow).where(
                AiModelPreferenceRow.provider_id == provider_id,
                AiModelPreferenceRow.model_id == model_id,
            )
        )
        if existing:
            raise ValueError(f"Model {model_id} is already in the {existing.tier} pool")

        max_priority = self.db.scalar(
            select(func.max(AiModelPreferenceRow.priority)).where(
                AiModelPreferenceRow.provider_id == provider_id,
                AiModelPreferenceRow.tier == tier,
            )
        )
        row = AiModelPreferenceRow(
            provider_id=provider_id,
            model_id=model_id,
            tier=tier,
            priority=int(max_priority or 0) + 1,
            enabled=enabled,
        )
        self.db.add(row)
        self.db.flush()
        _mirror_to_legacy(self.db, row)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(
        self,
        pref_id: int,
        *,
        enabled: bool | None = None,
        tier: str | None = None,
    ) -> AiModelPreferenceRow:
        row = self.db.get(AiModelPreferenceRow, pref_id)
        if row is None:
            raise LookupError("Model preference not found")
        if enabled is not None:
            row.enabled = enabled
        if tier is not None:
            if tier not in ("free", "paid"):
                raise ValueError("tier must be free or paid")
            if tier != row.tier:
                conflict = self.db.scalar(
                    select(AiModelPreferenceRow).where(
                        AiModelPreferenceRow.provider_id == row.provider_id,
                        AiModelPreferenceRow.model_id == row.model_id,
                        AiModelPreferenceRow.id != row.id,
                    )
                )
                if conflict:
                    raise ValueError("Model already exists in the other pool")
                max_priority = self.db.scalar(
                    select(func.max(AiModelPreferenceRow.priority)).where(
                        AiModelPreferenceRow.provider_id == row.provider_id,
                        AiModelPreferenceRow.tier == tier,
                    )
                )
                row.tier = tier
                row.priority = int(max_priority or 0) + 1
        self.db.add(row)
        _mirror_to_legacy(self.db, row)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, pref_id: int) -> None:
        row = self.db.get(AiModelPreferenceRow, pref_id)
        if row is None:
            raise LookupError("Model preference not found")
        model_id = row.model_id
        provider_id = row.provider_id
        self.db.delete(row)
        self.db.flush()
        _delete_legacy(self.db, model_id, provider_id)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()

    def reorder(self, *, tier: str, ordered_ids: list[int], provider_id: str | None = None) -> list[AiModelPreferenceRow]:
        if tier not in ("free", "paid"):
            raise ValueError("tier must be free or paid")
        rows = list_preferences(self.db, tier=tier, provider_id=provider_id)
        by_id = {r.id: r for r in rows}
        if set(ordered_ids) != set(by_id.keys()):
            raise ValueError("ordered_ids must include every model in the pool exactly once")
        for index, pref_id in enumerate(ordered_ids, start=1):
            row = by_id[pref_id]
            row.priority = index
            self.db.add(row)
            _mirror_to_legacy(self.db, row)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()
        return list_preferences(self.db, tier=tier, provider_id=provider_id)
