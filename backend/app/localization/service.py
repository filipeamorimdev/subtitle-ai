"""Localization task CRUD, ensure, cancel, retry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import JobRow, LocalizationTaskRow, MediaItemRow
from app.languages import Language, LanguageNormalizationError, normalize_language
from app.localization.checkpoints import default_checkpoints
from app.localization.state import (
    ACTIVE_STATUSES,
    InvalidTaskTransition,
    assert_transition,
)
from app.services.ai_cost import effective_cost_micro, micro_to_usd
from app.db.models import AiUsageRecordRow


SUPPORTED_CAPABILITIES = frozenset({"subtitles"})
EXECUTABLE_CAPABILITIES = frozenset({"subtitles"})


class UnsupportedCapabilityError(ValueError):
    def __init__(self, capability: str) -> None:
        if capability == "audio":
            msg = "This localization capability is not available."
        elif capability == "metadata":
            msg = "This localization capability is not available."
        else:
            msg = "This localization capability is not available."
        super().__init__(msg)
        self.capability = capability
        self.code = "unsupported_capability"


class ActiveTaskExistsError(ValueError):
    def __init__(self, task_id: int) -> None:
        super().__init__("An active localization task already exists for this media and language.")
        self.task_id = task_id
        self.code = "active_task_exists"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalizationTaskService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, task_id: int) -> LocalizationTaskRow | None:
        return self.db.get(LocalizationTaskRow, task_id)

    def find_active(
        self,
        media_item_id: int,
        target_language_code: str,
        capability: str = "subtitles",
    ) -> LocalizationTaskRow | None:
        return self.db.scalar(
            select(LocalizationTaskRow).where(
                LocalizationTaskRow.media_item_id == media_item_id,
                LocalizationTaskRow.target_language_code == target_language_code,
                LocalizationTaskRow.capability == capability,
                LocalizationTaskRow.status.in_(list(ACTIVE_STATUSES)),
            )
        )

    def transition(
        self,
        task: LocalizationTaskRow,
        status: str,
        *,
        substate: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> LocalizationTaskRow:
        assert_transition(task.status, status)
        now = utcnow()
        if task.status != status:
            if status == "planning" and task.started_at is None:
                task.started_at = now
            if status in {"processing", "verifying", "waiting_for_source"} and task.started_at is None:
                task.started_at = now
            if status in {"completed", "failed", "cancelled", "blocked"}:
                task.completed_at = now
            task.status = status
        if substate is not None:
            task.substate = substate
        elif status in {"completed", "cancelled", "failed", "blocked"}:
            task.substate = None
        if clear_error:
            task.error_code = None
            task.error_message = None
        if error_code is not None:
            task.error_code = error_code
        if error_message is not None:
            task.error_message = error_message
        task.updated_at = now
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def ensure_task(
        self,
        *,
        media_item: MediaItemRow,
        language: Language,
        capability: str = "subtitles",
        origin: str = "manual",
        requested_by: str | None = None,
        create_if_missing: bool = True,
    ) -> tuple[LocalizationTaskRow, bool]:
        """Return (task, created). Reuses an active task when present."""
        if capability not in EXECUTABLE_CAPABILITIES:
            raise UnsupportedCapabilityError(capability)

        existing = self.find_active(media_item.id, language.code, capability)
        if existing is not None:
            return existing, False
        if not create_if_missing:
            raise ActiveTaskExistsError(0)

        origin_norm = origin if origin in {"manual", "automatic"} else "manual"
        priority = "high" if origin_norm == "manual" else "normal"
        task = LocalizationTaskRow(
            media_item_id=media_item.id,
            target_language_code=language.code,
            target_language_name=language.display_name,
            capability=capability,
            status="requested",
            origin=origin_norm,
            priority=priority,
            requested_by=requested_by,
            metadata_json={"checkpoints": default_checkpoints()},
        )
        self.db.add(task)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            # Race on partial unique index — reuse winner.
            existing = self.find_active(media_item.id, language.code, capability)
            if existing is not None:
                return existing, False
            raise
        self.db.refresh(task)
        return task, True

    def create_manual_task(
        self,
        *,
        media_item: MediaItemRow,
        target_language: str,
        capability: str = "subtitles",
        requested_by: str | None = None,
        reuse_active: bool = False,
    ) -> tuple[LocalizationTaskRow, bool]:
        """Create a manual task. Raises ActiveTaskExistsError unless reuse_active."""
        if capability not in EXECUTABLE_CAPABILITIES:
            raise UnsupportedCapabilityError(capability)
        try:
            language = normalize_language(target_language)
        except LanguageNormalizationError:
            raise

        existing = self.find_active(media_item.id, language.code, capability)
        if existing is not None:
            if reuse_active:
                return existing, False
            raise ActiveTaskExistsError(existing.id)

        task, created = self.ensure_task(
            media_item=media_item,
            language=language,
            capability=capability,
            origin="manual",
            requested_by=requested_by,
        )
        if not created and not reuse_active:
            raise ActiveTaskExistsError(task.id)
        return task, created

    def list_tasks(
        self,
        *,
        status: str | None = None,
        origin: str | None = None,
        capability: str | None = None,
        language: str | None = None,
        media_type: str | None = None,
        media_item_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = False,
    ) -> list[LocalizationTaskRow]:
        query = select(LocalizationTaskRow).order_by(
            LocalizationTaskRow.created_at.desc(), LocalizationTaskRow.id.desc()
        )
        if status:
            query = query.where(LocalizationTaskRow.status == status)
        if active_only:
            query = query.where(LocalizationTaskRow.status.in_(list(ACTIVE_STATUSES)))
        if origin:
            query = query.where(LocalizationTaskRow.origin == origin)
        if capability:
            query = query.where(LocalizationTaskRow.capability == capability)
        if language:
            query = query.where(LocalizationTaskRow.target_language_code == language)
        if media_item_id is not None:
            query = query.where(LocalizationTaskRow.media_item_id == media_item_id)
        if media_type:
            query = query.join(MediaItemRow).where(MediaItemRow.media_type == media_type)
        query = query.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list(self.db.scalars(query).all())

    def count_tasks(
        self,
        *,
        status: str | None = None,
        origin: str | None = None,
        capability: str | None = None,
        language: str | None = None,
        media_type: str | None = None,
        media_item_id: int | None = None,
        active_only: bool = False,
    ) -> int:
        query = select(func.count()).select_from(LocalizationTaskRow)
        if status:
            query = query.where(LocalizationTaskRow.status == status)
        if active_only:
            query = query.where(LocalizationTaskRow.status.in_(list(ACTIVE_STATUSES)))
        if origin:
            query = query.where(LocalizationTaskRow.origin == origin)
        if capability:
            query = query.where(LocalizationTaskRow.capability == capability)
        if language:
            query = query.where(LocalizationTaskRow.target_language_code == language)
        if media_item_id is not None:
            query = query.where(LocalizationTaskRow.media_item_id == media_item_id)
        if media_type:
            query = query.join(MediaItemRow).where(MediaItemRow.media_type == media_type)
        return int(self.db.scalar(query) or 0)

    def latest_task_for_language(
        self,
        media_item_id: int,
        language_code: str,
        capability: str = "subtitles",
    ) -> LocalizationTaskRow | None:
        """Active task wins; otherwise the latest historical task for this language."""
        from app.subtitles.filenames import language_chip_matches_task

        active = self.find_active(media_item_id, language_code, capability)
        if active is not None:
            return active
        rows = list(
            self.db.scalars(
                select(LocalizationTaskRow)
                .where(
                    LocalizationTaskRow.media_item_id == media_item_id,
                    LocalizationTaskRow.capability == capability,
                )
                .order_by(LocalizationTaskRow.created_at.desc(), LocalizationTaskRow.id.desc())
            ).all()
        )
        for row in rows:
            if row.status in ACTIVE_STATUSES and language_chip_matches_task(
                row.target_language_code, language_code
            ):
                return row
        for row in rows:
            if language_chip_matches_task(row.target_language_code, language_code):
                return row
        return None

    def list_active(self) -> list[LocalizationTaskRow]:
        return list(
            self.db.scalars(
                select(LocalizationTaskRow).where(
                    LocalizationTaskRow.status.in_(list(ACTIVE_STATUSES))
                )
            ).all()
        )

    def jobs_for_task(self, task_id: int) -> list[JobRow]:
        return list(
            self.db.scalars(
                select(JobRow)
                .where(JobRow.task_id == task_id)
                .order_by(JobRow.created_at.asc(), JobRow.id.asc())
            ).all()
        )

    def attach_job(self, job: JobRow, task_id: int) -> JobRow:
        if getattr(job, "task_id", None) != task_id:
            job.task_id = task_id
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
        return job

    def update_checkpoints(self, task_id: int | None, **states: str) -> None:
        if not task_id:
            return
        from app.localization.checkpoints import merge_checkpoints

        task = self.get(int(task_id))
        if task is None:
            return
        task.metadata_json = merge_checkpoints(task.metadata_json, states)
        self.db.add(task)
        self.db.commit()

    def cancel(self, task_id: int) -> LocalizationTaskRow:
        task = self.get(task_id)
        if task is None:
            raise ValueError("Task not found")
        if task.status in {"completed", "cancelled"}:
            return task
        try:
            assert_transition(task.status, "cancelled")
        except InvalidTaskTransition as exc:
            raise ValueError(str(exc)) from exc

        # Cancel pending jobs; leave processing to finish safely.
        pending = self.db.scalars(
            select(JobRow).where(
                JobRow.task_id == task_id,
                JobRow.status == "pending",
            )
        ).all()
        now = utcnow()
        for job in pending:
            job.status = "cancelled"
            job.completed_at = now
            job.progress_detail = "Cancelled with localization task"
            job.reason_code = "cancelled"
            self.db.add(job)

        task.status = "cancelled"
        task.substate = None
        task.completed_at = now
        task.updated_at = now
        task.error_code = "cancelled"
        task.error_message = "Cancelled by user"
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def prepare_retry(self, task_id: int) -> LocalizationTaskRow:
        task = self.get(task_id)
        if task is None:
            raise ValueError("Task not found")
        if task.status not in {"failed", "blocked", "cancelled"}:
            if task.status in ACTIVE_STATUSES:
                return task
            if task.status == "completed":
                raise ValueError("Completed tasks cannot be retried; create a new request instead.")
        task.status = "planning"
        task.substate = "retry"
        task.error_code = None
        task.error_message = None
        task.completed_at = None
        task.updated_at = utcnow()
        if task.started_at is None:
            task.started_at = utcnow()
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def ai_summary(self, task_id: int) -> dict[str, Any]:
        job_ids = [j.id for j in self.jobs_for_task(task_id)]
        if not job_ids:
            return {
                "requests": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "provider_id": None,
                "model_id": None,
            }
        records = list(
            self.db.scalars(
                select(AiUsageRecordRow).where(AiUsageRecordRow.job_id.in_(job_ids))
            ).all()
        )
        total_micro = 0
        tokens = 0
        provider_id = None
        model_id = None
        for rec in records:
            total_micro += effective_cost_micro(rec) or 0
            tokens += int(rec.total_tokens or 0)
            if provider_id is None:
                provider_id = rec.provider_id
            if model_id is None:
                model_id = rec.model_id
        # Prefer last successful translation job's model if present.
        translate_jobs = [
            j
            for j in self.jobs_for_task(task_id)
            if j.job_kind == "translate" and j.status == "completed"
        ]
        if translate_jobs:
            last = translate_jobs[-1]
            provider_id = getattr(last, "provider_id", None) or provider_id
            model_id = last.model or model_id
        return {
            "requests": len(records),
            "tokens": tokens,
            "cost_usd": round(micro_to_usd(total_micro) or 0.0, 6),
            "provider_id": provider_id,
            "model_id": model_id,
        }
