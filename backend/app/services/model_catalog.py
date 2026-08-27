"""Provider-aware model catalog cache, pricing classification, and compatibility."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError
from app.ai.models import (
    CAPABILITY_TEXT_GENERATION,
    AIModel,
    PricingFreshness,
    PricingTier,
)
from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.ai.providers.registry import get_provider_registry
from app.core.logging import get_logger
from app.db.models import AiModelCatalogCacheRow, OpenRouterCatalogCacheRow

logger = get_logger("model_catalog")

CATALOG_TTL = timedelta(hours=6)
MIN_REFRESH_INTERVAL = timedelta(minutes=5)
SYSTEM_PROMPT_TOKEN_BUDGET = 2_000
TOKENS_PER_BLOCK_ESTIMATE = 80
COMPATIBLE_REASON = "Compatible with Subtitle AI translation"
INCOMPATIBLE_REASON = "Not compatible with Subtitle AI translation"

# Per-provider in-flight refresh locks (process-local; no Redis).
_refresh_locks: dict[str, asyncio.Lock] = {}
_last_refresh_at: dict[str, datetime] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pricing_freshness(fetched_at: datetime | None, *, stale: bool = False) -> PricingFreshness:
    """Derive freshness from catalog_fetched_at — not permanently stored."""
    if fetched_at is None:
        return PricingFreshness.UNKNOWN
    if stale:
        return PricingFreshness.STALE
    ts = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=timezone.utc)
    if utcnow() - ts >= CATALOG_TTL:
        return PricingFreshness.STALE
    return PricingFreshness.FRESH


def classify_pricing_tier(
    prompt_price_per_million: float | None,
    completion_price_per_million: float | None,
) -> str:
    if prompt_price_per_million is None or completion_price_per_million is None:
        return PricingTier.UNKNOWN.value
    if prompt_price_per_million <= 0 and completion_price_per_million <= 0:
        return PricingTier.FREE.value
    return PricingTier.PAID.value


def estimate_required_context(batch_size: int) -> int:
    return SYSTEM_PROMPT_TOKEN_BUDGET + max(1, batch_size) * TOKENS_PER_BLOCK_ESTIMATE * 2


def check_compatibility(
    model: AIModel | Any,
    *,
    batch_size: int = 25,
) -> tuple[bool, str]:
    """Return (compatible, reason). Uses capabilities, not hardcoded names."""
    if not isinstance(model, AIModel):
        # Accept legacy OpenRouterModelInfo in tests / transitional call sites.
        from app.ai.providers.openrouter import normalize_openrouter_model

        model = normalize_openrouter_model(model)

    if CAPABILITY_TEXT_GENERATION not in model.capabilities:
        # If modalities were never advertised, still allow (legacy catalog miss).
        inputs = model.metadata.get("input_modalities") if model.metadata else None
        outputs = model.metadata.get("output_modalities") if model.metadata else None
        if inputs or outputs:
            return False, INCOMPATIBLE_REASON

    inputs = {str(m).lower() for m in (model.metadata.get("input_modalities") or [])} if model.metadata else set()
    outputs = {str(m).lower() for m in (model.metadata or {}).get("output_modalities") or []} if model.metadata else set()

    if inputs and "text" not in inputs:
        return False, INCOMPATIBLE_REASON
    if outputs and "text" not in outputs:
        return False, INCOMPATIBLE_REASON
    if outputs and "text" not in outputs and any(x in outputs for x in ("image", "audio", "embedding")):
        return False, INCOMPATIBLE_REASON

    required = estimate_required_context(batch_size)
    if model.context_length is not None and model.context_length < required:
        return False, INCOMPATIBLE_REASON

    return True, COMPATIBLE_REASON


def _batch_base_model(model_id: str) -> str:
    from app.translation.openrouter.client import batch_base_model

    return batch_base_model(model_id)


def _model_to_cache_dict(model: AIModel) -> dict[str, Any]:
    return {
        "provider_id": model.provider_id,
        "id": model.model_id,
        "model_id": model.model_id,
        "name": model.name,
        "prompt_price_per_million": float(model.input_price_per_million)
        if model.input_price_per_million is not None
        else None,
        "completion_price_per_million": float(model.output_price_per_million)
        if model.output_price_per_million is not None
        else None,
        "context_length": model.context_length,
        "description": model.description,
        "capabilities": sorted(model.capabilities),
        "pricing_tier": model.pricing_tier.value
        if isinstance(model.pricing_tier, PricingTier)
        else str(model.pricing_tier),
        "available": model.available,
        "deprecated": model.deprecated,
        "deprecation_date": model.deprecation_date.isoformat() if model.deprecation_date else None,
        "sunset_date": model.sunset_date.isoformat() if model.sunset_date else None,
        "replacement_model_id": model.replacement_model_id,
        "input_modalities": model.metadata.get("input_modalities") if model.metadata else None,
        "output_modalities": model.metadata.get("output_modalities") if model.metadata else None,
        "architecture": model.metadata.get("architecture") if model.metadata else None,
    }


def _model_from_cache_dict(data: dict[str, Any], *, default_provider: str) -> AIModel | None:
    model_id = data.get("model_id") or data.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    provider_id = str(data.get("provider_id") or default_provider)
    caps_raw = data.get("capabilities")
    if isinstance(caps_raw, list) and caps_raw:
        capabilities = {str(c) for c in caps_raw}
    else:
        capabilities = {CAPABILITY_TEXT_GENERATION}
        if model_id.endswith(":batch"):
            from app.ai.models import CAPABILITY_BATCH

            capabilities.add(CAPABILITY_BATCH)

    in_price = data.get("prompt_price_per_million")
    out_price = data.get("completion_price_per_million")
    try:
        in_dec = Decimal(str(in_price)) if in_price is not None else None
    except Exception:  # noqa: BLE001
        in_dec = None
    try:
        out_dec = Decimal(str(out_price)) if out_price is not None else None
    except Exception:  # noqa: BLE001
        out_dec = None

    tier = PricingTier.from_value(data.get("pricing_tier"))
    if tier == PricingTier.UNKNOWN and in_dec is not None and out_dec is not None:
        tier = PricingTier.from_value(classify_pricing_tier(float(in_dec), float(out_dec)))

    metadata: dict[str, Any] = {}
    if isinstance(data.get("input_modalities"), list):
        metadata["input_modalities"] = list(data["input_modalities"])
    if isinstance(data.get("output_modalities"), list):
        metadata["output_modalities"] = list(data["output_modalities"])
    if isinstance(data.get("architecture"), dict):
        metadata["architecture"] = data["architecture"]

    sunset = None
    if isinstance(data.get("sunset_date"), str):
        try:
            sunset = datetime.fromisoformat(data["sunset_date"])
        except ValueError:
            sunset = None

    deprecation = None
    if isinstance(data.get("deprecation_date"), str):
        try:
            deprecation = datetime.fromisoformat(data["deprecation_date"])
        except ValueError:
            deprecation = None

    return AIModel(
        provider_id=provider_id,
        model_id=model_id,
        name=str(data.get("name") or model_id),
        context_length=data.get("context_length")
        if isinstance(data.get("context_length"), int)
        else None,
        input_price_per_million=in_dec,
        output_price_per_million=out_dec,
        capabilities=capabilities,
        pricing_tier=tier,
        available=bool(data.get("available", True)),
        deprecated=bool(data.get("deprecated", False)),
        deprecation_date=deprecation,
        sunset_date=sunset,
        replacement_model_id=data.get("replacement_model_id")
        if isinstance(data.get("replacement_model_id"), str)
        else None,
        description=data.get("description") if isinstance(data.get("description"), str) else None,
        metadata=metadata,
    )


@dataclass
class CatalogSnapshot:
    provider_id: str
    models: list[AIModel]
    fetched_at: datetime | None
    stale: bool
    age_seconds: int | None = None

    @property
    def freshness(self) -> PricingFreshness:
        return pricing_freshness(self.fetched_at, stale=self.stale)


class ModelCatalogService:
    """Provider-neutral catalog. Fetches via provider adapters — no hardcoded URLs."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_for(self, provider_id: str) -> asyncio.Lock:
        if provider_id not in _refresh_locks:
            _refresh_locks[provider_id] = asyncio.Lock()
        return _refresh_locks[provider_id]

    def _load_cache_row(self, provider_id: str) -> AiModelCatalogCacheRow | None:
        return self.db.get(AiModelCatalogCacheRow, provider_id)

    def _models_from_row(self, row: AiModelCatalogCacheRow) -> list[AIModel]:
        payload = row.payload_json
        if not isinstance(payload, list):
            return []
        models: list[AIModel] = []
        for item in payload:
            if isinstance(item, dict):
                model = _model_from_cache_dict(item, default_provider=row.provider_id)
                if model is not None:
                    models.append(model)
        return models

    def get_cached(self, provider_id: str = OPENROUTER_PROVIDER_ID) -> CatalogSnapshot | None:
        row = self._load_cache_row(provider_id)
        if row is None and provider_id == OPENROUTER_PROVIDER_ID:
            # Legacy fallback.
            legacy = self.db.get(OpenRouterCatalogCacheRow, 1)
            if legacy is None:
                return None
            models: list[AIModel] = []
            payload = legacy.payload_json
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        # Legacy OpenRouterModelInfo shape.
                        model = _model_from_cache_dict(item, default_provider=provider_id)
                        if model is not None:
                            models.append(model)
            fetched = legacy.fetched_at
            if fetched and fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            age = int((utcnow() - fetched).total_seconds()) if fetched else None
            return CatalogSnapshot(
                provider_id=provider_id,
                models=models,
                fetched_at=fetched,
                stale=bool(legacy.stale),
                age_seconds=age,
            )

        if row is None:
            return None
        fetched = row.fetched_at
        if fetched and fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = int((utcnow() - fetched).total_seconds()) if fetched else None
        return CatalogSnapshot(
            provider_id=provider_id,
            models=self._models_from_row(row),
            fetched_at=fetched,
            stale=bool(row.stale),
            age_seconds=age,
        )

    def is_fresh(self, snapshot: CatalogSnapshot | None = None, *, provider_id: str = OPENROUTER_PROVIDER_ID) -> bool:
        snap = snapshot or self.get_cached(provider_id)
        if snap is None or snap.fetched_at is None or snap.stale:
            return False
        return pricing_freshness(snap.fetched_at, stale=snap.stale) == PricingFreshness.FRESH

    async def refresh(self, provider_id: str = OPENROUTER_PROVIDER_ID) -> list[AIModel]:
        snap = await self.get_models(provider_id=provider_id, force_refresh=True)
        return snap.models

    async def refresh_all(self) -> dict[str, list[AIModel]]:
        out: dict[str, list[AIModel]] = {}
        for provider in get_provider_registry().enabled():
            out[provider.provider_id] = await self.refresh(provider.provider_id)
        return out

    async def get_models(
        self,
        *,
        provider_id: str = OPENROUTER_PROVIDER_ID,
        force_refresh: bool = False,
        api_key: str | None = None,  # kept for call-site compatibility; unused
    ) -> CatalogSnapshot:
        del api_key  # credentials come from the provider adapter
        cached = self.get_cached(provider_id)
        if not force_refresh and cached and self.is_fresh(cached, provider_id=provider_id):
            return cached

        lock = self._lock_for(provider_id)
        async with lock:
            # Re-check after acquiring lock (another coroutine may have refreshed).
            cached = self.get_cached(provider_id)
            if not force_refresh and cached and self.is_fresh(cached, provider_id=provider_id):
                return cached

            last = _last_refresh_at.get(provider_id)
            if (
                not force_refresh
                and last is not None
                and utcnow() - last < MIN_REFRESH_INTERVAL
                and cached is not None
            ):
                # Minimum interval for background/automatic refresh only.
                return cached

            try:
                from app.ai.bootstrap import bootstrap_providers
                from app.ai.providers.openrouter import OpenRouterProvider

                bootstrap_providers(self.db)
                provider = get_provider_registry().get(provider_id)
                # Prefer provider.list_models; for OpenRouter allow direct client path
                # so tests can monkeypatch OpenRouterClient.list_models.
                if isinstance(provider, OpenRouterProvider) or provider_id == OPENROUTER_PROVIDER_ID:
                    from app.ai.credentials.service import ProviderAccountService
                    from app.translation.openrouter.client import OpenRouterClient

                    try:
                        key = ProviderAccountService(self.db).get_api_key(provider_id)
                    except Exception:  # noqa: BLE001
                        key = None
                    if key is None:
                        # Avoid Fernet/config side-effects when only listing public models.
                        try:
                            from app.db.models import SettingsRow

                            settings = self.db.get(SettingsRow, 1)
                            if settings and settings.openrouter_api_key_encrypted:
                                from app.core.config import get_app_config
                                from app.core.secrets import decrypt_secret, load_or_create_fernet

                                key = decrypt_secret(
                                    load_or_create_fernet(get_app_config().secret_key_path),
                                    settings.openrouter_api_key_encrypted,
                                )
                        except Exception:  # noqa: BLE001
                            key = None
                    from app.translation.openrouter.client import OpenRouterError as ORError

                    try:
                        from app.services.ai_usage import AiUsageService, make_openrouter_http_usage_hook

                        hook = make_openrouter_http_usage_hook(
                            AiUsageService(self.db),
                            job_id=None,
                            trigger_type="system",
                            default_operation="catalog_list",
                            provider_id=provider_id,
                        )
                        infos = await OpenRouterClient.list_models(
                            api_key=key or None,
                            usage_hook=hook,
                        )
                        from app.ai.providers.openrouter import normalize_openrouter_model

                        models = [normalize_openrouter_model(info) for info in infos]
                    except ORError as exc:
                        from app.ai.providers.openrouter import openrouter_error_to_provider_error

                        raise openrouter_error_to_provider_error(exc) from exc
                else:
                    models = await provider.list_models()
                payload = [_model_to_cache_dict(m) for m in models]
                row = self._load_cache_row(provider_id)
                if row is None:
                    row = AiModelCatalogCacheRow(
                        provider_id=provider_id, payload_json=payload, stale=False
                    )
                else:
                    row.payload_json = payload
                    row.stale = False
                row.fetched_at = utcnow()
                self.db.add(row)

                # Mirror OpenRouter into legacy cache for alpha1 compatibility.
                if provider_id == OPENROUTER_PROVIDER_ID:
                    legacy = self.db.get(OpenRouterCatalogCacheRow, 1)
                    legacy_payload = [
                        {
                            "id": m.model_id,
                            "name": m.name,
                            "prompt_price_per_million": float(m.input_price_per_million)
                            if m.input_price_per_million is not None
                            else None,
                            "completion_price_per_million": float(m.output_price_per_million)
                            if m.output_price_per_million is not None
                            else None,
                            "context_length": m.context_length,
                            "description": m.description,
                            "input_modalities": (m.metadata or {}).get("input_modalities"),
                            "output_modalities": (m.metadata or {}).get("output_modalities"),
                            "architecture": (m.metadata or {}).get("architecture"),
                            "pricing_tier": m.pricing_tier.value
                            if isinstance(m.pricing_tier, PricingTier)
                            else str(m.pricing_tier),
                        }
                        for m in models
                    ]
                    if legacy is None:
                        legacy = OpenRouterCatalogCacheRow(
                            id=1, payload_json=legacy_payload, stale=False
                        )
                    else:
                        legacy.payload_json = legacy_payload
                        legacy.stale = False
                    legacy.fetched_at = row.fetched_at
                    self.db.add(legacy)

                self.db.commit()
                _last_refresh_at[provider_id] = utcnow()
                return CatalogSnapshot(
                    provider_id=provider_id,
                    models=models,
                    fetched_at=row.fetched_at,
                    stale=False,
                    age_seconds=0,
                )
            except AIProviderError as exc:
                logger.warning("Catalog refresh failed provider=%s: %s", provider_id, exc)
                if cached is not None:
                    row = self._load_cache_row(provider_id)
                    if row is not None:
                        row.stale = True
                        self.db.add(row)
                        self.db.commit()
                    return CatalogSnapshot(
                        provider_id=provider_id,
                        models=cached.models,
                        fetched_at=cached.fetched_at,
                        stale=True,
                        age_seconds=cached.age_seconds,
                    )
                # Preserve OpenRouterError for legacy tests when no cache exists.
                original = getattr(exc, "original_error", None)
                if original is not None:
                    raise original from exc
                raise

    def get_model(
        self,
        provider_id_or_model_id: str,
        model_id: str | None = None,
    ) -> AIModel | None:
        """Lookup by (provider_id, model_id) or legacy single model_id (OpenRouter)."""
        if model_id is None:
            provider_id = OPENROUTER_PROVIDER_ID
            mid = provider_id_or_model_id
        else:
            provider_id = provider_id_or_model_id
            mid = model_id
        base = _batch_base_model(mid)
        cached = self.get_cached(provider_id)
        if cached is None:
            return None
        for model in cached.models:
            if model.model_id == mid or model.model_id == base:
                return model
        return None

    def models(self, provider_id: str = OPENROUTER_PROVIDER_ID) -> list[AIModel]:
        cached = self.get_cached(provider_id)
        return list(cached.models) if cached else []

    def annotate_model(
        self,
        model_id: str,
        *,
        batch_size: int = 25,
        provider_id: str = OPENROUTER_PROVIDER_ID,
    ) -> dict[str, Any]:
        """Metadata for UI: tier, compatibility, availability, freshness."""
        cached = self.get_cached(provider_id)
        info = self.get_model(provider_id, model_id)
        stale = bool(cached.stale) if cached else False
        freshness = pricing_freshness(
            cached.fetched_at if cached else None, stale=stale
        ).value
        if info is None:
            return {
                "provider_id": provider_id,
                "model_id": model_id,
                "name": model_id,
                "pricing_tier": PricingTier.UNKNOWN.value,
                "prompt_price_per_million": None,
                "completion_price_per_million": None,
                "context_length": None,
                "compatible": True,
                "compatibility_reason": "Catalog metadata unavailable",
                "available": False if cached and not stale else True,
                "unavailable": bool(cached and not stale),
                "stale": stale,
                "pricing_freshness": freshness,
                "description": None,
                "deprecated": False,
                "sunset_date": None,
                "replacement_model_id": None,
                "capabilities": [CAPABILITY_TEXT_GENERATION],
            }
        compatible, reason = check_compatibility(info, batch_size=batch_size)
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "name": info.name,
            "pricing_tier": info.pricing_tier.value
            if isinstance(info.pricing_tier, PricingTier)
            else str(info.pricing_tier),
            "prompt_price_per_million": info.prompt_price_per_million,
            "completion_price_per_million": info.completion_price_per_million,
            "context_length": info.context_length,
            "compatible": compatible,
            "compatibility_reason": reason,
            "available": info.available,
            "unavailable": not info.available,
            "stale": stale,
            "pricing_freshness": freshness,
            "description": info.description,
            "input_modalities": (info.metadata or {}).get("input_modalities"),
            "output_modalities": (info.metadata or {}).get("output_modalities"),
            "deprecated": info.deprecated,
            "deprecation_date": info.deprecation_date.isoformat() if info.deprecation_date else None,
            "sunset_date": info.sunset_date.isoformat() if info.sunset_date else None,
            "replacement_model_id": info.replacement_model_id,
            "capabilities": sorted(info.capabilities),
        }


# Alias for documentation / future imports.
AIModelCatalogService = ModelCatalogService
