"""Background job worker (per job-kind concurrency)."""

from __future__ import annotations

import asyncio
import time

from app.core.logging import get_logger
from app.db import get_session_factory
from app.db.models import JobRow
from app.jobs.service import JobService
from app.services.settings import SettingsService
from sqlalchemy import select

logger = get_logger("worker")

JOB_KINDS = ("translate", "extract", "request", "transcribe", "dub")
TASK_REPLAN_INTERVAL_SECONDS = 30.0
# Upper bounds are deliberately above the normal operational timeouts in each
# provider.  They are a last-resort circuit breaker for a coroutine that never
# returns and would otherwise retain a worker slot forever.
JOB_MAX_RUNTIME_SECONDS = {
    "translate": 6 * 60 * 60.0,
    "extract": 2 * 60 * 60.0,
    "request": 15 * 60.0,
    "transcribe": 9 * 60 * 60.0,
    "dub": 6 * 60 * 60.0,
}


class JobWorker:
    def __init__(self, poll_interval: float = 2.0) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, set[asyncio.Task]] = {kind: set() for kind in JOB_KINDS}
        self._tasks_by_job: dict[int, asyncio.Task] = {}
        self._started_at: dict[int, float] = {}
        self._kinds_by_job: dict[int, str] = {}
        self._last_task_replan = 0.0
        self._limits_cache: tuple[float, dict[str, int]] = (0.0, {})

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        session = get_session_factory()()
        try:
            recovered = JobService.recover_interrupted_jobs(session)
            if recovered:
                logger.info("Recovered %s interrupted job(s) after restart", recovered)
        finally:
            session.close()
        self._stop = asyncio.Event()
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
        self._started_at.clear()
        self._kinds_by_job.clear()
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
            self._started_at.pop(job_id, None)
            self._kinds_by_job.pop(job_id, None)
        for kind in JOB_KINDS:
            self._inflight[kind] = {task for task in self._inflight[kind] if not task.done()}

    def _cancelled_inflight_ids(self) -> set[int]:
        if not self._tasks_by_job:
            return set()
        session = get_session_factory()()
        try:
            rows = session.scalars(
                select(JobRow.id).where(
                    JobRow.id.in_(list(self._tasks_by_job)),
                    JobRow.status == "cancelled",
                )
            ).all()
            return set(rows)
        finally:
            session.close()

    def _reconcile_cancelled_slots(self) -> None:
        """Cancel in-flight asyncio tasks whose DB row was cancelled.

        Do not cancel jobs that have already been marked completed (or failed) in
        the database: translate jobs commit ``completed`` before the Bazarr
        verify backoff finishes, and cancelling that tail leaves tasks stuck
        in ``verifying`` with no ``job_end`` / verify result.
        """
        cancelled = self._cancelled_inflight_ids()
        for job_id, task in list(self._tasks_by_job.items()):
            if job_id not in cancelled or task.done():
                continue
            logger.warning(
                "Releasing worker slot for cancelled job_id=%s",
                job_id,
            )
            task.cancel()

    def _cancel_overdue_jobs(self) -> None:
        now = time.monotonic()
        for job_id, task in list(self._tasks_by_job.items()):
            if task.done():
                continue
            started = self._started_at.get(job_id, now)
            kind = self._kinds_by_job.get(job_id, "translate")
            limit = JOB_MAX_RUNTIME_SECONDS.get(kind or "translate", JOB_MAX_RUNTIME_SECONDS["translate"])
            if now - started < limit:
                continue
            elapsed = int(now - started)
            logger.error(
                "Job exceeded runtime limit job_id=%s kind=%s elapsed=%ss limit=%ss",
                job_id,
                kind,
                elapsed,
                int(limit),
            )
            # Persist terminal state before cancelling.  This prevents an
            # orphan recovery loop from immediately retrying a hung operation.
            JobService.fail_job_from_worker(
                job_id,
                TimeoutError(f"Job exceeded its {int(limit)} second runtime limit."),
            )
            task.cancel()

    def _concurrency_limits(self) -> dict[str, int]:
        now = time.monotonic()
        cached_at, cached = self._limits_cache
        if cached and now - cached_at < 5.0:
            return cached
        session = get_session_factory()()
        try:
            limits = SettingsService(session).concurrency_limits()
        finally:
            session.close()
        self._limits_cache = (now, limits)
        return limits

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
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            JobService.fail_job_from_worker(job_id, exc)
        finally:
            session.close()
            self._tasks_by_job.pop(job_id, None)

    def _recover_orphaned_jobs(self) -> None:
        session = get_session_factory()()
        try:
            recovered = JobService.recover_orphaned_processing_jobs(
                session, inflight_ids=set(self._tasks_by_job)
            )
            if recovered:
                logger.warning("Recovered %s orphaned processing job(s)", recovered)
        except Exception as exc:  # noqa: BLE001
            logger.error("Orphan recovery failed: %s", exc)
        finally:
            session.close()

    async def _replan_active_tasks(self) -> None:
        """Resume localization tasks that have no in-flight job (e.g. extract finished)."""
        from app.localization.planner import TaskPlanner

        session = get_session_factory()()
        try:
            await TaskPlanner(session).plan_all_active()
        except Exception as exc:  # noqa: BLE001
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("Active task replan failed: %s", exc)
        finally:
            session.close()

    async def _tick(self) -> None:
        self._prune_inflight()
        self._reconcile_cancelled_slots()
        self._cancel_overdue_jobs()
        self._prune_inflight()
        now = time.monotonic()
        if now - self._last_task_replan >= TASK_REPLAN_INTERVAL_SECONDS:
            self._last_task_replan = now
            await self._replan_active_tasks()
            self._prune_inflight()
        self._recover_orphaned_jobs()
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
                self._started_at[job_id] = time.monotonic()
                self._kinds_by_job[job_id] = kind


worker = JobWorker()
