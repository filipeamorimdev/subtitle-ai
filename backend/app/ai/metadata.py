"""Credential-safe metadata allowlisting for provider responses."""

from __future__ import annotations

from typing import Any

# Safe keys that may appear in AIResponse.raw_metadata.
METADATA_ALLOWLIST = frozenset(
    {
        "request_id",
        "provider_request_id",
        "finish_reason",
        "response_model",
        "usage",
        "batch_id",
        "provider_status",
        "latency_ms",
        "native_finish_reason",
    }
)

_SECRET_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "credential",
        "bearer",
    }
)

_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "content",
        "subtitle",
        "subtitles",
        "system",
        "user",
        "input",
        "output",
        "choices",
        "body",
        "request",
        "response",
    }
)


def sanitize_metadata(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only allowlisted keys; drop secrets and full content payloads."""
    if not data:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if lowered not in METADATA_ALLOWLIST:
            continue
        if any(frag in lowered for frag in _SECRET_KEY_FRAGMENTS):
            continue
        cleaned[key] = _redact_value(value)
    return cleaned or None


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[redacted]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(frag in lowered for frag in _SECRET_KEY_FRAGMENTS):
                continue
            if lowered in _CONTENT_KEYS and isinstance(item, (str, list)):
                out[key] = "[redacted]"
                continue
            out[key] = _redact_value(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        if len(value) > 20:
            return f"[list:{len(value)}]"
        return [_redact_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:100] + "…[truncated]"
    return value


def redact_secrets_from_text(text: str | None) -> str | None:
    """Defensive last-pass: never log obvious API-key shaped strings."""
    if text is None:
        return None
    # Do not attempt complex regex replacement of prompts — callers should
    # avoid logging them. This only trims extremely long blobs.
    if len(text) > 2000:
        return text[:200] + "…[truncated]"
    return text
