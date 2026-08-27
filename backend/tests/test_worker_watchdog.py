"""Worker liveness circuit-breaker tests."""

from __future__ import annotations

import asyncio

import pytest

from app.jobs.worker import JOB_MAX_RUNTIME_SECONDS, JobWorker


@pytest.mark.asyncio
async def test_overdue_job_is_failed_and_cancelled(monkeypatch):
    worker = JobWorker()
    sleeper = asyncio.create_task(asyncio.Event().wait())
    worker._tasks_by_job[12] = sleeper
    worker._started_at[12] = 0.0
    worker._kinds_by_job[12] = "translate"
    monkeypatch.setitem(JOB_MAX_RUNTIME_SECONDS, "translate", 0.0)
    failed: list[tuple[int, BaseException]] = []

    def fake_fail(job_id: int, exc: BaseException) -> None:
        failed.append((job_id, exc))

    monkeypatch.setattr("app.jobs.worker.JobService.fail_job_from_worker", fake_fail)
    worker._cancel_overdue_jobs()
    await asyncio.gather(sleeper, return_exceptions=True)

    assert failed and failed[0][0] == 12
    assert isinstance(failed[0][1], TimeoutError)
