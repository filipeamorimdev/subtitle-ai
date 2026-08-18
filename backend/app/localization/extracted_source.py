"""Cleanup of sidecar SRTs created by extracting an embedded track.

Those files duplicate the in-video track in Jellyfin. They must stay until the
target language is verified in Bazarr, then be removed just before the task ends.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRow, LocalizationTaskRow
from app.subtitles.filenames import unlink_extracted_source

__all__ = [
    "EXTRACTED_EMBEDDED_REASON",
    "EXTRACTED_SOURCE_META_KEY",
    "metadata_extracted_source",
    "remember_extracted_source",
    "resolve_extracted_source_path",
    "unlink_extracted_source",
]

EXTRACTED_SOURCE_META_KEY = "extracted_source_path"
EXTRACTED_EMBEDDED_REASON = "extracted_embedded"


def remember_extracted_source(metadata: dict | None, path: str) -> dict:
    meta = dict(metadata or {})
    meta[EXTRACTED_SOURCE_META_KEY] = path
    return meta


def metadata_extracted_source(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    value = metadata.get(EXTRACTED_SOURCE_META_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


def resolve_extracted_source_path(
    db: Session,
    *,
    task_id: int | None = None,
    translate_job: JobRow | None = None,
) -> Path | None:
    """Return the sidecar this run extracted, if any."""
    if task_id is not None:
        task = db.get(LocalizationTaskRow, task_id)
        if task is not None:
            stored = metadata_extracted_source(task.metadata_json)
            if stored:
                return Path(stored)
        extract = _latest_job(db, task_id=task_id, job_kind="extract", status="completed")
        if extract and extract.target_subtitle_path:
            return Path(extract.target_subtitle_path)
        request = _latest_job(
            db,
            task_id=task_id,
            job_kind="request",
            status="completed",
            reason_code=EXTRACTED_EMBEDDED_REASON,
        )
        if request and request.target_subtitle_path:
            return Path(request.target_subtitle_path)
        return None

    if translate_job is None:
        return None
    source = translate_job.source_subtitle_path
    candidate_key = translate_job.candidate_key
    extract = _latest_matching_source_job(
        db,
        job_kind="extract",
        status="completed",
        source_path=source,
        candidate_key=candidate_key,
        media_path=translate_job.media_path,
    )
    if extract and extract.target_subtitle_path:
        return Path(extract.target_subtitle_path)
    request = _latest_matching_source_job(
        db,
        job_kind="request",
        status="completed",
        source_path=source,
        candidate_key=candidate_key,
        media_path=translate_job.media_path,
        reason_code=EXTRACTED_EMBEDDED_REASON,
    )
    if request and request.target_subtitle_path:
        return Path(request.target_subtitle_path)
    return None


def _latest_job(
    db: Session,
    *,
    task_id: int,
    job_kind: str,
    status: str,
    reason_code: str | None = None,
) -> JobRow | None:
    clauses = [
        JobRow.task_id == task_id,
        JobRow.job_kind == job_kind,
        JobRow.status == status,
    ]
    if reason_code is not None:
        clauses.append(JobRow.reason_code == reason_code)
    return db.scalar(
        select(JobRow).where(*clauses).order_by(JobRow.created_at.desc(), JobRow.id.desc()).limit(1)
    )


def _latest_matching_source_job(
    db: Session,
    *,
    job_kind: str,
    status: str,
    source_path: str,
    candidate_key: str | None,
    media_path: str | None,
    reason_code: str | None = None,
) -> JobRow | None:
    clauses = [
        JobRow.job_kind == job_kind,
        JobRow.status == status,
        JobRow.target_subtitle_path == source_path,
    ]
    if reason_code is not None:
        clauses.append(JobRow.reason_code == reason_code)
    if candidate_key:
        clauses.append(JobRow.candidate_key == candidate_key)
    elif media_path:
        clauses.append(JobRow.media_path == media_path)
    else:
        return None
    return db.scalar(
        select(JobRow).where(*clauses).order_by(JobRow.created_at.desc(), JobRow.id.desc()).limit(1)
    )
