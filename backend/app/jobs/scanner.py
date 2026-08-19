"""Background automatic subtitle fallback scanner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.api.schemas import AutomationScanResult, AutomationStatusOut
from app.core.logging import get_logger
from app.db import get_session_factory
from app.services.fallback import FallbackPlanner
from app.services.settings import SettingsService

logger = get_logger("scanner")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutomaticScanner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_scan_at: datetime | None = None
        self._next_scan_at: datetime | None = None
        self._last_result: AutomationScanResult | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="automatic-scanner")
        logger.info("Automatic scanner started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        logger.info("Automatic scanner stopped")

    def status(self) -> AutomationStatusOut:
        session = get_session_factory()()
        try:
            enabled = SettingsService(session).is_automatic_fallback_enabled()
        finally:
            session.close()
        return AutomationStatusOut(
            enabled=enabled,
            scanner_running=self.running,
            last_scan_at=self._last_scan_at,
            next_scan_at=self._next_scan_at,
            last_result=self._last_result,
        )

    def _scan_interval_seconds(self) -> float:
        session = get_session_factory()()
        try:
            minutes = SettingsService(session).get_public().automatic_scan_interval_minutes
        finally:
            session.close()
        return max(60.0, float(minutes) * 60.0)

    async def scan_once(self) -> AutomationScanResult:
        async with self._lock:
            session = get_session_factory()()
            try:
                result = await FallbackPlanner(session).scan_once()
            finally:
                session.close()
            self._last_scan_at = utcnow()
            self._last_result = result
            interval = self._scan_interval_seconds()
            self._next_scan_at = self._last_scan_at + timedelta(seconds=interval)
            logger.info(
                "Automatic scan finished ok=%s created=%s reused=%s skipped=%s errors=%s",
                result.ok,
                result.created_count,
                result.reused_count,
                result.skipped_count,
                len(result.errors),
            )
            return result

    async def _run(self) -> None:
        # Initial next_scan hint
        self._next_scan_at = utcnow() + timedelta(seconds=self._scan_interval_seconds())
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Automatic scanner loop error: %s", exc)
                self._last_result = AutomationScanResult(
                    ok=False,
                    message=str(exc),
                    scanned_at=utcnow(),
                    enabled=True,
                    errors=[str(exc)],
                )
            interval = self._scan_interval_seconds()
            self._next_scan_at = utcnow() + timedelta(seconds=interval)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue


scanner = AutomaticScanner()
