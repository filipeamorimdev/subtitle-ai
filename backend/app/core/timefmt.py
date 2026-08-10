"""Consistent datetime formatting: Y-m-d H:i:s."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_datetime(value: datetime | None) -> str | None:
    """Format a datetime as ``YYYY-MM-DD HH:MM:SS`` (UTC)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime(DATETIME_FORMAT)


def utcnow_formatted() -> str:
    return format_datetime(datetime.now(timezone.utc)) or ""


DateTimeOut = Annotated[
    datetime | None,
    PlainSerializer(format_datetime, return_type=str | None, when_used="json"),
]
