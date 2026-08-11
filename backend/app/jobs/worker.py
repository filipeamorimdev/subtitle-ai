"""Background job worker (per job-kind concurrency)."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db import get_session_factory
from app.jobs.service import JobService
from app.services.settings import SettingsService

logger = get_logger("worker")

JOB_KINDS = ("translate", "extract", "request")


class JobWorker:
    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, set[asyncio.Task]] = {kind: set() for kind in JOB_KINDS}

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
        pending = [task for tasks in self._inflight.values() for task in tasks]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for kind in JOB_KINDS:
            self._inflight[kind].clear()
        logger.info("Job worker stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("Worker loop error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    def _prune_inflight(self) -> None:
        for kind in JOB_KINDS:
            self._inflight[kind] = {task for task in self._inflight[kind] if not task.done()}

    def _concurrency_limits(self) -> dict[str, int]:
        session = get_session_factory()()
        try:
            return SettingsService(session).concurrency_limits()
        finally:
            session.close()

    def _claim(self, job_kind: str):
        session = get_session_factory()()
        try:
            return JobService(session).claim_next_job(job_kind=job_kind)
        finally:
            session.close()

    async def _process(self, job_id: int) -> None:
        session = get_session_factory()()
        try:
            await JobService(session).process_job(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Job %s failed in worker: %s", job_id, exc)
        finally:
            session.close()

    async def _tick(self) -> None:
        self._prune_inflight()
        limits = self._concurrency_limits()
        for kind in JOB_KINDS:
            limit = limits.get(kind, 1)
            while len(self._inflight[kind]) < limit:
                job = self._claim(kind)
                if not job:
                    break
                task = asyncio.create_task(self._process(job.id), name=f"job-{kind}-{job.id}")
                self._inflight[kind].add(task)


worker = JobWorker()
