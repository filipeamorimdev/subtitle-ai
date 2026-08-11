"""Background job worker (per job-kind concurrency)."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db import get_session_factory
from app.db.models import JobRow
from app.jobs.service import JobService
from app.services.settings import SettingsService
from sqlalchemy import select

logger = get_logger("worker")

JOB_KINDS = ("translate", "extract", "request")


class JobWorker:
    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, set[asyncio.Task]] = {kind: set() for kind in JOB_KINDS}
        self._tasks_by_job: dict[int, asyncio.Task] = {}

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
        pending = list(self._tasks_by_job.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for kind in JOB_KINDS:
            self._inflight[kind].clear()
        self._tasks_by_job.clear()
        logger.info("Job worker stopped")

    def cancel_job(self, job_id: int) -> bool:
        """Cancel the in-flight asyncio task for a job, freeing its concurrency slot."""
        task = self._tasks_by_job.get(job_id)
        if task is None or task.done():
            return False
        loop = task.get_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()
        logger.info("Cancelled in-flight worker task job_id=%s", job_id)
        return True

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
        done_ids = [job_id for job_id, task in self._tasks_by_job.items() if task.done()]
        for job_id in done_ids:
            self._tasks_by_job.pop(job_id, None)
        for kind in JOB_KINDS:
            self._inflight[kind] = {task for task in self._inflight[kind] if not task.done()}

    def _active_processing_ids(self) -> set[int]:
        if not self._tasks_by_job:
            return set()
        session = get_session_factory()()
        try:
            rows = session.scalars(
                select(JobRow.id).where(
                    JobRow.id.in_(list(self._tasks_by_job)),
                    JobRow.status == "processing",
                )
            ).all()
            return set(rows)
        finally:
            session.close()

    def _reconcile_cancelled_slots(self) -> None:
        """Drop slots held by tasks whose DB row is no longer processing (e.g. cancelled)."""
        active = self._active_processing_ids()
        for job_id, task in list(self._tasks_by_job.items()):
            if job_id in active or task.done():
                continue
            logger.warning(
                "Releasing worker slot for job_id=%s (no longer processing)",
                job_id,
            )
            task.cancel()

    def _concurrency_limits(self) -> dict[str, int]:
        session = get_session_factory()()
        try:
            return SettingsService(session).concurrency_limits()
        finally:
            session.close()

    def _claim(self, job_kind: str) -> int | None:
        session = get_session_factory()()
        try:
            job = JobService(session).claim_next_job(job_kind=job_kind)
            return job.id if job else None
        finally:
            session.close()

    async def _process(self, job_id: int) -> None:
        session = get_session_factory()()
        try:
            await JobService(session).process_job(job_id)
        except asyncio.CancelledError:
            logger.info("Job task cancelled job_id=%s", job_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Job %s failed in worker: %s", job_id, exc)
        finally:
            session.close()
            self._tasks_by_job.pop(job_id, None)

    async def _tick(self) -> None:
        self._prune_inflight()
        self._reconcile_cancelled_slots()
        self._prune_inflight()
        limits = self._concurrency_limits()
        for kind in JOB_KINDS:
            limit = limits.get(kind, 1)
            while len(self._inflight[kind]) < limit:
                job_id = self._claim(kind)
                if job_id is None:
                    break
                task = asyncio.create_task(self._process(job_id), name=f"job-{kind}-{job_id}")
                self._inflight[kind].add(task)
                self._tasks_by_job[job_id] = task


worker = JobWorker()
