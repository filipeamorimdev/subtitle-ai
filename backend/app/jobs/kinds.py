"""Dispatch a claimed job to the kind-specific processor on JobService.

Queue operations live in ``app.jobs.queue``.
Bazarr upload/rescan lives in ``app.jobs.bazarr_sync``.
Translate prompt/draft helpers live in ``app.jobs.translate``.
Extract/request/transcribe processors remain JobService methods and are
invoked from here so process_job has a single orchestration point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.events import publish_job
from app.db.models import JobRow

if TYPE_CHECKING:
    from app.jobs.service import JobService


async def process_claimed_job(svc: JobService, job_id: int) -> None:
    row = svc.db.get(JobRow, job_id)
    if not row or row.status != "processing":
        return
    kind = getattr(row, "job_kind", None) or "translate"
    publish_job(
        job_id=job_id,
        task_id=getattr(row, "task_id", None),
        status="processing",
        progress=row.progress,
        job_kind=kind,
        detail=row.progress_detail,
    )
    try:
        if kind == "extract":
            await svc._process_extract_job(job_id)
        elif kind == "request":
            await svc._process_request_subtitle_job(job_id)
        elif kind == "transcribe":
            await svc._process_transcribe_job(job_id)
        else:
            await svc._process_translate_job(job_id)
    finally:
        await svc._notify_task_planner(job_id)
        done = svc.db.get(JobRow, job_id)
        if done is not None:
            publish_job(
                job_id=job_id,
                task_id=getattr(done, "task_id", None),
                status=done.status,
                progress=done.progress,
                job_kind=done.job_kind,
                detail=done.progress_detail,
            )
