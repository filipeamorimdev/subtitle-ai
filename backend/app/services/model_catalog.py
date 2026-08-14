"""OpenRouter model catalog cache, pricing classification, and compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import OpenRouterCatalogCacheRow
from app.services.settings import SettingsService
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError, OpenRouterModelInfo, batch_base_model

logger = get_logger("model_catalog")

CATALOG_TTL = timedelta(hours=6)
# Rough tokens for system prompt + per-block overhead when estimating context needs.
SYSTEM_PROMPT_TOKEN_BUDGET = 2_000
TOKENS_PER_BLOCK_ESTIMATE = 80
COMPATIBLE_REASON = "Compatible with Subtitle AI translation"
INCOMPATIBLE_REASON = "Not compatible with Subtitle AI translation"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_pricing_tier(
    prompt_price_per_million: float | None,
    completion_price_per_million: float | None,
) -> str:
    if prompt_price_per_million is None or completion_price_per_million is None:
        return "unknown"
    if prompt_price_per_million <= 0 and completion_price_per_million <= 0:
        return "free"
    return "paid"


def estimate_required_context(batch_size: int) -> int:
    return SYSTEM_PROMPT_TOKEN_BUDGET + max(1, batch_size) * TOKENS_PER_BLOCK_ESTIMATE * 2


def check_compatibility(
    model: OpenRouterModelInfo,
    *,
    batch_size: int = 25,
) -> tuple[bool, str]:
    """Return (compatible, reason). No hardcoded model-name allowlist."""
    inputs = {m.lower() for m in (model.input_modalities or [])}
    outputs = {m.lower() for m in (model.output_modalities or [])}

    # If modalities are advertised, require text in and text out.
    if inputs and "text" not in inputs:
        return False, INCOMPATIBLE_REASON
    if outputs and "text" not in outputs:
        return False, INCOMPATIBLE_REASON

    # Image-/audio-only style models without text are incompatible.
    if outputs and "text" not in outputs and any(x in outputs for x in ("image", "audio", "embedding")):
        return False, INCOMPATIBLE_REASON

    required = estimate_required_context(batch_size)
    if model.context_length is not None and model.context_length < required:
        return False, INCOMPATIBLE_REASON

    return True, COMPATIBLE_REASON


@dataclass
class CatalogSnapshot:
    models: list[OpenRouterModelInfo]
    fetched_at: datetime | None
    stale: bool
    age_seconds: int | None = None


class ModelCatalogService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _load_cache_row(self) -> OpenRouterCatalogCacheRow | None:
        return self.db.get(OpenRouterCatalogCacheRow, 1)

    def _models_from_row(self, row: OpenRouterCatalogCacheRow) -> list[OpenRouterModelInfo]:
        payload = row.payload_json
        if not isinstance(payload, list):
            return []
        models: list[OpenRouterModelInfo] = []
        for item in payload:
            if isinstance(item, dict) and item.get("id"):
                try:
                    models.append(OpenRouterModelInfo.from_dict(item))
                except Exception:  # noqa: BLE001
                    continue
        return models

    def get_cached(self) -> CatalogSnapshot | None:
        row = self._load_cache_row()
        if row is None:
            return None
        fetched = row.fetched_at
        if fetched and fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = None
        if fetched:
            age = int((utcnow() - fetched).total_seconds())
        return CatalogSnapshot(
            models=self._models_from_row(row),
            fetched_at=fetched,
            stale=bool(row.stale),
            age_seconds=age,
        )

    def is_fresh(self, snapshot: CatalogSnapshot | None = None) -> bool:
        snap = snapshot or self.get_cached()
        if snap is None or snap.fetched_at is None or snap.stale:
            return False
        return utcnow() - snap.fetched_at < CATALOG_TTL

    async def get_models(
        self,
        *,
        force_refresh: bool = False,
        api_key: str | None = None,
    ) -> CatalogSnapshot:
        cached = self.get_cached()
        if not force_refresh and cached and self.is_fresh(cached):
            return cached

        if api_key is None:
            key, _ = SettingsService(self.db).get_openrouter_credentials()
            api_key = key

        try:
            models = await OpenRouterClient.list_models(api_key=api_key or None)
            payload = [m.to_dict() for m in models]
            row = self._load_cache_row()
            if row is None:
                row = OpenRouterCatalogCacheRow(id=1, payload_json=payload, stale=False)
            else:
                row.payload_json = payload
                row.stale = False
            row.fetched_at = utcnow()
            self.db.add(row)
            self.db.commit()
            return CatalogSnapshot(
                models=models,
                fetched_at=row.fetched_at,
                stale=False,
                age_seconds=0,
            )
        except OpenRouterError as exc:
            logger.warning("OpenRouter catalog refresh failed: %s", exc)
            if cached is not None:
                row = self._load_cache_row()
                if row is not None:
                    row.stale = True
                    self.db.add(row)
                    self.db.commit()
                return CatalogSnapshot(
                    models=cached.models,
                    fetched_at=cached.fetched_at,
                    stale=True,
                    age_seconds=cached.age_seconds,
                )
            raise

    def get_model(self, model_id: str) -> OpenRouterModelInfo | None:
        base = batch_base_model(model_id)
        cached = self.get_cached()
        if cached is None:
            return None
        for model in cached.models:
            if model.id == model_id or model.id == base:
                return model
        return None

    def annotate_model(
        self,
        model_id: str,
        *,
        batch_size: int = 25,
    ) -> dict[str, Any]:
        """Metadata for UI: tier, compatibility, availability, freshness."""
        cached = self.get_cached()
        info = self.get_model(model_id)
        stale = bool(cached.stale) if cached else False
        if info is None:
            return {
                "model_id": model_id,
                "name": model_id,
                "pricing_tier": "unknown",
                "prompt_price_per_million": None,
                "completion_price_per_million": None,
                "context_length": None,
                "compatible": True,  # unknown catalog: don't block configured models solely for refresh fail
                "compatibility_reason": "Catalog metadata unavailable",
                "available": False if cached and not stale else True,
                "unavailable": bool(cached and not stale),
                "stale": stale,
                "description": None,
            }
        compatible, reason = check_compatibility(info, batch_size=batch_size)
        return {
            "model_id": model_id,
            "name": info.name,
            "pricing_tier": info.pricing_tier,
            "prompt_price_per_million": info.prompt_price_per_million,
            "completion_price_per_million": info.completion_price_per_million,
            "context_length": info.context_length,
            "compatible": compatible,
            "compatibility_reason": reason,
            "available": True,
            "unavailable": False,
            "stale": stale,
            "description": info.description,
            "input_modalities": info.input_modalities,
            "output_modalities": info.output_modalities,
        }
