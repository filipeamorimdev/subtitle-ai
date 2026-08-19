"""Cached live probes for Bazarr / OpenRouter (not just 'configured')."""

from __future__ import annotations

import threading
import time
from typing import Any

_TTL_SECONDS = 60.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, str, dict[str, Any] | None]] = {}


def _get_cached(key: str) -> tuple[str, dict[str, Any] | None] | None:
    with _lock:
        row = _cache.get(key)
        if row is None:
            return None
        expires, status, details = row
        if time.monotonic() > expires:
            return None
        return status, details


def _set_cached(key: str, status: str, details: dict[str, Any] | None = None) -> str:
    with _lock:
        _cache[key] = (time.monotonic() + _TTL_SECONDS, status, details)
    return status


def invalidate_probes(*keys: str) -> None:
    with _lock:
        if not keys:
            _cache.clear()
            return
        for key in keys:
            _cache.pop(key, None)


def invalidate_ai_connection_health(provider_id: str = "openrouter") -> None:
    """Drop live-probe and provider ping caches after routing or credential changes."""
    invalidate_probes(provider_id)
    try:
        from app.ai.providers.registry import get_provider_registry

        provider = get_provider_registry().get_optional(provider_id)
        if provider is not None:
            provider.invalidate_health_cache()
    except Exception:  # noqa: BLE001
        pass


async def probe_bazarr(db) -> str:
    cached = _get_cached("bazarr")
    if cached is not None:
        return cached[0]
    from app.integrations.bazarr.client import BazarrClient, BazarrError
    from app.services.settings import SettingsService

    url, key = SettingsService(db).get_bazarr_credentials()
    if not url:
        return _set_cached("bazarr", "not_configured")
    try:
        await BazarrClient(url, key, timeout=2.0).test_connection()
        return _set_cached("bazarr", "ok")
    except BazarrError:
        return _set_cached("bazarr", "unreachable")
    except Exception:  # noqa: BLE001
        return _set_cached("bazarr", "unreachable")


async def probe_openrouter(db) -> str:
    cached = _get_cached("openrouter")
    if cached is not None:
        return cached[0]
    from app.services.settings import SettingsService

    public = SettingsService(db).get_public()
    if not public.openrouter_api_key_configured:
        return _set_cached("openrouter", "not_configured")
    try:
        from app.ai.bootstrap import bootstrap_providers
        from app.ai.models import ProviderStatus
        from app.ai.providers.registry import get_provider_registry
        from app.services.model_router import first_eligible_ping_model

        bootstrap_providers(db)
        candidate = first_eligible_ping_model(db)
        if candidate is None:
            return _set_cached("openrouter", "unreachable")
        provider = get_provider_registry().get_optional("openrouter")
        if provider is None:
            return _set_cached("openrouter", "unreachable")
        result = await provider.test_connection(candidate.model_id)
        status = getattr(result, "status", None)
        if status == ProviderStatus.CONNECTED:
            return _set_cached("openrouter", "ok")
        if status == ProviderStatus.CONFIGURED:
            return _set_cached("openrouter", "configured")
        return _set_cached("openrouter", "unreachable")
    except Exception:  # noqa: BLE001
        return _set_cached("openrouter", "unreachable")
