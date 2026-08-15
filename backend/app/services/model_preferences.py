"""OpenRouter model preference pools and legacy migration."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import OpenRouterModelPreferenceRow, SettingsRow
from app.translation.openrouter.client import batch_base_model

logger = get_logger("model_preferences")


def _classify_tier_from_cache(db: Session, model_id: str) -> str:
    """Best-effort tier from catalog cache; unknown if unavailable."""
    try:
        from app.services.model_catalog import ModelCatalogService

        catalog = ModelCatalogService(db)
        info = catalog.get_model(model_id)
        if info is None:
            return "unknown"
        return info.pricing_tier
    except Exception:  # noqa: BLE001
        return "unknown"


def seed_legacy_model_preference(db: Session) -> OpenRouterModelPreferenceRow | None:
    """
    If no preferences exist, seed from settings.openrouter_model.

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
    # Prefer storing the configured slug (including :batch) as the preference id.
    pref_model_id = model_id
    tier = _classify_tier_from_cache(db, base_id)
    if tier == "unknown":
        # Heuristic for common free suffixes when catalog is empty at migrate time.
        lowered = base_id.lower()
        if ":free" in lowered or lowered.endswith("/free"):
            tier = "free"
        else:
            # Default unknown/paid models into the paid pool so paid_only still selects them.
            tier = "paid"

    row = OpenRouterModelPreferenceRow(
        model_id=pref_model_id,
        tier="free" if tier == "free" else "paid",
        priority=1,
        enabled=True,
    )
    db.add(row)

    # Preserve working behavior for upgrades.
    if tier == "free":
        settings.routing_strategy = "free_only"
    else:
        settings.routing_strategy = "paid_only"
    settings.allow_paid_fallback = False
    # Keep allow_free_fallback at default True (harmless for paid_only).

    db.add(settings)
    logger.info(
        "Seeded legacy openrouter_model=%s as tier=%s strategy=%s",
        pref_model_id,
        row.tier,
        settings.routing_strategy,
    )
    return row


def list_preferences(
    db: Session,
    *,
    tier: str | None = None,
    enabled_only: bool = False,
) -> list[OpenRouterModelPreferenceRow]:
    query = select(OpenRouterModelPreferenceRow)
    if tier:
        query = query.where(OpenRouterModelPreferenceRow.tier == tier)
    if enabled_only:
        query = query.where(OpenRouterModelPreferenceRow.enabled.is_(True))
    query = query.order_by(
        OpenRouterModelPreferenceRow.tier.asc(),
        OpenRouterModelPreferenceRow.priority.asc(),
        OpenRouterModelPreferenceRow.id.asc(),
    )
    return list(db.scalars(query).all())


def sync_legacy_openrouter_model(db: Session) -> None:
    """Keep settings.openrouter_model aligned with the first enabled preference."""
    settings = db.get(SettingsRow, 1)
    if settings is None:
        return
    prefs = list_preferences(db, enabled_only=True)
    if not prefs:
        return
    # Prefer free pool first model, else first paid.
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
        self.db.flush()

    def list_all(self, *, enabled_only: bool = False) -> list[OpenRouterModelPreferenceRow]:
        self.ensure_seeded()
        return list_preferences(self.db, enabled_only=enabled_only)

    def add(
        self,
        *,
        model_id: str,
        tier: str,
        enabled: bool = True,
    ) -> OpenRouterModelPreferenceRow:
        model_id = model_id.strip()
        if tier not in ("free", "paid"):
            raise ValueError("tier must be free or paid")
        catalog_tier = _classify_tier_from_cache(self.db, batch_base_model(model_id))
        if catalog_tier == "paid" and tier == "free":
            raise ValueError(
                f"Model {model_id} is priced as paid in the catalog and cannot be added to the free pool"
            )
        if catalog_tier == "free" and tier == "paid":
            raise ValueError(
                f"Model {model_id} is priced as free in the catalog and cannot be added to the paid pool"
            )
        existing = self.db.scalar(
            select(OpenRouterModelPreferenceRow).where(
                OpenRouterModelPreferenceRow.model_id == model_id
            )
        )
        if existing:
            raise ValueError(f"Model {model_id} is already in the {existing.tier} pool")

        max_priority = self.db.scalar(
            select(func.max(OpenRouterModelPreferenceRow.priority)).where(
                OpenRouterModelPreferenceRow.tier == tier
            )
        )
        row = OpenRouterModelPreferenceRow(
            model_id=model_id,
            tier=tier,
            priority=int(max_priority or 0) + 1,
            enabled=enabled,
        )
        self.db.add(row)
        self.db.flush()
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
    ) -> OpenRouterModelPreferenceRow:
        row = self.db.get(OpenRouterModelPreferenceRow, pref_id)
        if row is None:
            raise LookupError("Model preference not found")
        if enabled is not None:
            row.enabled = enabled
        if tier is not None:
            if tier not in ("free", "paid"):
                raise ValueError("tier must be free or paid")
            # Moving pools: assign next priority in target pool.
            if tier != row.tier:
                conflict = self.db.scalar(
                    select(OpenRouterModelPreferenceRow).where(
                        OpenRouterModelPreferenceRow.model_id == row.model_id,
                        OpenRouterModelPreferenceRow.id != row.id,
                    )
                )
                if conflict:
                    raise ValueError("Model already exists in the other pool")
                max_priority = self.db.scalar(
                    select(func.max(OpenRouterModelPreferenceRow.priority)).where(
                        OpenRouterModelPreferenceRow.tier == tier
                    )
                )
                row.tier = tier
                row.priority = int(max_priority or 0) + 1
        self.db.add(row)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, pref_id: int) -> None:
        row = self.db.get(OpenRouterModelPreferenceRow, pref_id)
        if row is None:
            raise LookupError("Model preference not found")
        self.db.delete(row)
        self.db.flush()
        sync_legacy_openrouter_model(self.db)
        self.db.commit()

    def reorder(self, *, tier: str, ordered_ids: list[int]) -> list[OpenRouterModelPreferenceRow]:
        if tier not in ("free", "paid"):
            raise ValueError("tier must be free or paid")
        rows = list_preferences(self.db, tier=tier)
        by_id = {r.id: r for r in rows}
        if set(ordered_ids) != set(by_id.keys()):
            raise ValueError("ordered_ids must include every model in the pool exactly once")
        for index, pref_id in enumerate(ordered_ids, start=1):
            row = by_id[pref_id]
            row.priority = index
            self.db.add(row)
        sync_legacy_openrouter_model(self.db)
        self.db.commit()
        return list_preferences(self.db, tier=tier)
