"""Background job worker (single concurrent job)."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db import get_session_factory
from app.jobs.service import JobService

logger = get_logger("worker")


class JobWorker:
    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._busy = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="job-worker")
        logger.info("Job worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        logger.info("Job worker stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._busy:
                    await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("Worker loop error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        session = get_session_factory()()
        try:
            service = JobService(session)
            job = service.claim_next_job()
            if not job:
                return
            self._busy = True
            job_id = job.id
        finally:
            session.close()

        session = get_session_factory()()
        try:
            service = JobService(session)
            await service.process_job(job_id)
        finally:
            session.close()
            self._busy = False


worker = JobWorker()
