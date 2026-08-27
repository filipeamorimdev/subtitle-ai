"""Optional HTTP Basic and reverse-proxy forward-auth."""

from __future__ import annotations

import secrets
from base64 import b64decode
from ipaddress import ip_address, ip_network
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_app_config

PUBLIC_PATHS = frozenset({"/api/health"})


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        content="Authentication required",
        headers={"WWW-Authenticate": 'Basic realm="Subtitle AI"'},
        media_type="text/plain",
    )


def _json_unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
        headers={"WWW-Authenticate": 'Basic realm="Subtitle AI"'},
    )


def _basic_ok(request: Request, username: str, password: str) -> bool:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    if ":" not in decoded:
        return False
    user, pwd = decoded.split(":", 1)
    return secrets.compare_digest(user, username) and secrets.compare_digest(pwd, password)


def _from_trusted_proxy(request: Request, trusted_proxies: Iterable[str] | None) -> bool:
    """Return true only when the direct ASGI peer is explicitly trusted.

    Do not inspect ``X-Forwarded-For`` here: it is itself client-controlled
    unless a trusted reverse proxy has already stripped it.
    """
    client = request.client
    if client is None or not trusted_proxies:
        return False
    try:
        peer = ip_address(client.host)
    except ValueError:
        return False
    for raw_network in trusted_proxies:
        try:
            if peer in ip_network(raw_network, strict=False):
                return True
        except ValueError:
            # An invalid configuration must fail closed.
            continue
    return False


def _forward_ok(
    request: Request,
    header_name: str,
    trusted_proxies: Iterable[str] | None,
) -> bool:
    if not _from_trusted_proxy(request, trusted_proxies):
        return False
    value = request.headers.get(header_name)
    return bool(value and value.strip())


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        config = get_app_config()
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/health"):
            return await call_next(request)

        password = (config.auth_password or "").strip()
        forward_header = (config.auth_forward_header or "").strip()
        if not password and not forward_header:
            return await call_next(request)

        if forward_header and _forward_ok(
            request,
            forward_header,
            config.auth_forward_trusted_proxies,
        ):
            return await call_next(request)
        if password and _basic_ok(request, config.auth_username, password):
            return await call_next(request)

        if path.startswith("/api/"):
            return _json_unauthorized()
        return _unauthorized()
