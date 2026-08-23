"""Process runner cancellation and timeout tests."""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.media.process_runner import ProcessOutcome, run_process, run_process_checked


@pytest.mark.asyncio
async def test_run_process_completes():
    result = await run_process_checked([sys.executable, "-c", "print('ok')"], timeout_s=10)
    assert result.ok
    assert "ok" in result.stdout_text


@pytest.mark.asyncio
async def test_run_process_timeout_kills():
    result = await run_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_s=0.4,
        term_grace_s=0.2,
    )
    assert result.outcome is ProcessOutcome.TIMEOUT


@pytest.mark.asyncio
async def test_run_process_cancel_kills():
    cancelled = False

    def is_cancelled() -> bool:
        return cancelled

    async def flip() -> None:
        nonlocal cancelled
        await asyncio.sleep(0.2)
        cancelled = True

    asyncio.create_task(flip())
    result = await run_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        is_cancelled=is_cancelled,
        cancel_poll_s=0.05,
        term_grace_s=0.2,
        timeout_s=10,
    )
    assert result.outcome is ProcessOutcome.CANCELLED
