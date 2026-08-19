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
        from app.ai.models import ProviderStatus
        from app.ai.providers.registry import get_provider_registry

        provider = get_provider_registry().get_optional("openrouter")
        if provider is None:
            return _set_cached("openrouter", "configured")
        result = await provider.test_connection()
        status = getattr(result, "status", None)
        if status == ProviderStatus.CONNECTED:
            return _set_cached("openrouter", "ok")
        if status == ProviderStatus.CONFIGURED:
            return _set_cached("openrouter", "configured")
        return _set_cached("openrouter", "unreachable")
    except Exception:  # noqa: BLE001
        return _set_cached("openrouter", "configured")
