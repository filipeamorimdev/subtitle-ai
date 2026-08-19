"""In-process pub/sub for live UI updates (SSE)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

_subscribers: set[asyncio.Queue[str]] = set()
_lock = asyncio.Lock()


def _encode(event: dict[str, Any]) -> str:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    return json.dumps(payload, default=str)


async def subscribe() -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.add(queue)
    await queue.put(_encode({"type": "hello"}))
    return queue


async def unsubscribe(queue: asyncio.Queue[str]) -> None:
    async with _lock:
        _subscribers.discard(queue)


def publish(event: dict[str, Any]) -> None:
    """Best-effort fan-out. Safe to call from sync worker code."""
    encoded = _encode(event)
    stale: list[asyncio.Queue[str]] = []
    for queue in list(_subscribers):
        try:
            queue.put_nowait(encoded)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        _subscribers.discard(queue)


def publish_job(
    *,
    job_id: int | None,
    task_id: int | None = None,
    status: str | None = None,
    progress: float | None = None,
    detail: str | None = None,
    job_kind: str | None = None,
) -> None:
    publish(
        {
            "type": "job",
            "job_id": job_id,
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "detail": detail,
            "job_kind": job_kind,
        }
    )


def publish_task(
    *,
    task_id: int | None,
    status: str | None = None,
    substate: str | None = None,
    media_item_id: int | None = None,
) -> None:
    publish(
        {
            "type": "task",
            "task_id": task_id,
            "status": status,
            "substate": substate,
            "media_item_id": media_item_id,
        }
    )
