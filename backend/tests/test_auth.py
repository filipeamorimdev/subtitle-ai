"""Authentication boundary tests."""

from __future__ import annotations

from fastapi import Request

from app.api.auth import _forward_ok


def _request(*, peer: str, header: str | None = "alice") -> Request:
    headers = [] if header is None else [(b"x-forwarded-user", header.encode("utf-8"))]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jobs",
            "headers": headers,
            "client": (peer, 12345),
            "scheme": "http",
            "server": ("subtitle-ai", 6768),
        }
    )


def test_forward_auth_requires_a_trusted_direct_proxy():
    assert not _forward_ok(
        _request(peer="203.0.113.10"), "x-forwarded-user", ["127.0.0.1/32"]
    )
    assert not _forward_ok(_request(peer="127.0.0.1"), "x-forwarded-user", None)
    assert _forward_ok(_request(peer="127.0.0.1"), "x-forwarded-user", ["127.0.0.1/32"])


def test_forward_auth_rejects_empty_header_and_invalid_proxy_configuration():
    assert not _forward_ok(_request(peer="127.0.0.1", header=""), "x-forwarded-user", ["127.0.0.1"])
    assert not _forward_ok(
        _request(peer="127.0.0.1"), "x-forwarded-user", ["not-a-network"]
    )
