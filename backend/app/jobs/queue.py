"""Job queue operations (claim, recover, cancel, fail)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.events import publish_job
from app.core.logging import get_logger
from app.db.models import JobRow, LocalizationTaskRow

logger = get_logger("jobs.queue")

# Occupies a queue slot: claimed later (pending), running (processing), or held (paused).
OPEN_JOB_STATUSES: tuple[str, ...] = ("pending", "processing", "paused")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def claim_next_job(db: Session, job_kind: str | None = None) -> JobRow | None:
    query = (
        select(JobRow)
        .where(JobRow.status == "pending")
        .order_by(
            JobRow.trigger_type.desc(),
            JobRow.created_at.asc(),
        )
        .limit(1)
    )
    if job_kind:
        query = query.where(JobRow.job_kind == job_kind)
    row = db.scalar(query)
    if not row:
        return None
    result = db.execute(
        update(JobRow)
        .where(JobRow.id == row.id, JobRow.status == "pending")
        .values(
            status="processing",
            started_at=utcnow(),
            progress=0,
            progress_detail="Starting",
        )
    )
    if not result.rowcount:
        db.rollback()
        return None
    db.commit()
    db.refresh(row)
    task_id = getattr(row, "task_id", None)
    if task_id is not None:
        task = db.get(LocalizationTaskRow, task_id)
        if task is not None and task.status == "cancelled":
            row.status = "cancelled"
            row.completed_at = utcnow()
            row.progress_detail = "Cancelled with localization task"
            row.reason_code = "cancelled"
            db.add(row)
            db.commit()
            publish_job(
                job_id=row.id,
                task_id=task_id,
                status="cancelled",
                job_kind=row.job_kind,
            )
            return None
    publish_job(
        job_id=row.id,
        task_id=task_id,
        status="processing",
        progress=0,
        job_kind=row.job_kind,
    )
    return row


def recover_interrupted_jobs(db: Session) -> int:
    result = db.execute(
        update(JobRow)
        .where(JobRow.status == "processing")
        .values(
            status="pending",
            started_at=None,
            progress=0,
            progress_detail="Recovered after restart",
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def recover_orphaned_processing_jobs(db: Session, inflight_ids: set[int]) -> int:
    stmt = update(JobRow).where(JobRow.status == "processing")
    if inflight_ids:
        stmt = stmt.where(JobRow.id.notin_(list(inflight_ids)))
    result = db.execute(
        stmt.values(
            status="pending",
            started_at=None,
            progress=0,
            progress_detail="Recovered after worker lost the job",
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def fail_job_from_worker(job_id: int, exc: BaseException, *, public_error, reason_code) -> None:
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        row = session.get(JobRow, job_id)
        if row is None or row.status not in {"pending", "processing"}:
            return
        row.status = "failed"
        row.error = public_error(exc if isinstance(exc, Exception) else Exception(str(exc)))
        row.reason_code = reason_code(exc) if isinstance(exc, Exception) else "failed"
        row.completed_at = utcnow()
        if not row.progress_detail:
            row.progress_detail = "Worker failed"
        session.add(row)
        session.commit()
        publish_job(
            job_id=row.id,
            task_id=getattr(row, "task_id", None),
            status="failed",
            job_kind=row.job_kind,
            detail=row.progress_detail,
        )
    except Exception as persist_exc:  # noqa: BLE001
        session.rollback()
        logger.error(
            "Failed to persist worker failure job_id=%s error=%s",
            job_id,
            persist_exc,
        )
    finally:
        session.close()


def cancel_job_row(db: Session, job_id: int) -> JobRow:
    row = db.get(JobRow, job_id)
    if not row:
        raise ValueError("Job not found")
    if row.status not in OPEN_JOB_STATUSES:
        raise ValueError("Only pending, processing, or paused jobs can be cancelled")
    row.status = "cancelled"
    row.completed_at = utcnow()
    row.progress_detail = "Cancelled by user"
    row.reason_code = "cancelled"
    db.add(row)
    db.commit()
    publish_job(
        job_id=row.id,
        task_id=getattr(row, "task_id", None),
        status="cancelled",
        job_kind=row.job_kind,
    )
    return row


def pause_job_row(db: Session, job_id: int) -> JobRow:
    row = db.get(JobRow, job_id)
    if not row:
        raise ValueError("Job not found")
    if row.status != "pending":
        raise ValueError("Only pending jobs can be paused")
    row.status = "paused"
    row.progress_detail = "Paused"
    db.add(row)
    db.commit()
    publish_job(
        job_id=row.id,
        task_id=getattr(row, "task_id", None),
        status="paused",
        job_kind=row.job_kind,
        detail=row.progress_detail,
    )
    return row


def resume_job_row(db: Session, job_id: int) -> JobRow:
    row = db.get(JobRow, job_id)
    if not row:
        raise ValueError("Job not found")
    if row.status != "paused":
        raise ValueError("Only paused jobs can be resumed")
    row.status = "pending"
    row.progress_detail = "Queued"
    db.add(row)
    db.commit()
    publish_job(
        job_id=row.id,
        task_id=getattr(row, "task_id", None),
        status="pending",
        job_kind=row.job_kind,
        detail=row.progress_detail,
    )
    return row
