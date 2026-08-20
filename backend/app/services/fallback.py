"""Automatic subtitle fallback planner and observation store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AutomationScanResult,
    CandidateOut,
    ExtractCreate,
    JobCreate,
    JobOut,
)
from app.core.logging import get_logger
from app.db.models import JobRow, ObservedCandidateRow
from app.jobs.queue import OPEN_JOB_STATUSES
from app.integrations.bazarr.client import BazarrError
from app.ai.errors import AIProviderError
from app.services.candidates import CandidateService
from app.services.settings import SettingsService
from app.subtitles.embedded import EmbeddedError

logger = get_logger("fallback")

RETRYABLE_REASON_CODES = {
    "bazarr_error",
    "openrouter_error",
    "provider_error",
    "provider_timeout",
    "rate_limit",
    "failed",
    "bazarr_rescan_failed",
    "bazarr_verify_failed",
}

NON_RETRYABLE_REASON_CODES = {
    "target_exists",
    "cache_hit",
    "openrouter_auth",
    "provider_auth",
    "validation_failed",
    "not_found",
    "extract_failed",
    "cancelled",
    "blocked_by_cost_policy",
    "no_compatible_model",
    "unknown_pricing",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class FallbackPlanner:
    """Decide the next automatic job for Bazarr wanted candidates."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)

    def observe_candidate(self, candidate: CandidateOut, *, now: datetime | None = None) -> ObservedCandidateRow:
        now = now or utcnow()
        row = self.db.get(ObservedCandidateRow, candidate.key)
        if row is None:
            row = ObservedCandidateRow(
                candidate_key=candidate.key,
                media_type=candidate.media_type,
                media_path=candidate.media_path,
                media_title=candidate.title,
                target_language=candidate.target_language,
                bazarr_movie_id=candidate.bazarr_movie_id,
                bazarr_episode_id=candidate.bazarr_episode_id,
                bazarr_series_id=candidate.bazarr_series_id,
                first_seen_at=now,
                last_seen_at=now,
                automatic_attempts=0,
                currently_wanted=True,
            )
            self.db.add(row)
        else:
            # Reappeared after leaving wanted → restart grace period.
            if not row.currently_wanted:
                row.first_seen_at = now
                row.automatic_attempts = 0
                row.last_outcome = None
                row.last_reason_code = None
                row.last_automatic_attempt_at = None
            row.currently_wanted = True
            row.last_seen_at = now
            row.media_type = candidate.media_type
            row.media_path = candidate.media_path
            row.media_title = candidate.title
            row.target_language = candidate.target_language
            row.bazarr_movie_id = candidate.bazarr_movie_id
            row.bazarr_episode_id = candidate.bazarr_episode_id
            row.bazarr_series_id = candidate.bazarr_series_id
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def mark_not_wanted(self, present_keys: set[str], *, now: datetime | None = None) -> None:
        now = now or utcnow()
        rows = self.db.scalars(
            select(ObservedCandidateRow).where(ObservedCandidateRow.currently_wanted.is_(True))
        ).all()
        changed = False
        for row in rows:
            if row.candidate_key not in present_keys:
                row.currently_wanted = False
                row.last_seen_at = now
                self.db.add(row)
                changed = True
        if changed:
            self.db.commit()

    def grace_expired(self, observed: ObservedCandidateRow, grace_minutes: int, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        first_seen = _as_utc(observed.first_seen_at) or now
        return now >= first_seen + timedelta(minutes=max(0, grace_minutes))

    def _active_job(self, candidate_key: str, job_kind: str | None = None) -> JobRow | None:
        query = select(JobRow).where(
            JobRow.candidate_key == candidate_key,
            JobRow.status.in_(OPEN_JOB_STATUSES),
        )
        if job_kind:
            query = query.where(JobRow.job_kind == job_kind)
        return self.db.scalar(query.order_by(JobRow.created_at.desc()).limit(1))

    def _latest_job(self, candidate_key: str, job_kind: str) -> JobRow | None:
        return self.db.scalar(
            select(JobRow)
            .where(JobRow.candidate_key == candidate_key, JobRow.job_kind == job_kind)
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
            .limit(1)
        )

    def _latest_verify_failed_translate(self, candidate_key: str) -> JobRow | None:
        """Completed translate whose file exists but Bazarr still reports missing."""
        row = self.db.scalar(
            select(JobRow)
            .where(
                JobRow.candidate_key == candidate_key,
                JobRow.job_kind == "translate",
                JobRow.status == "completed",
                JobRow.reason_code == "bazarr_verify_failed",
            )
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        target = Path(row.target_subtitle_path)
        if not target.is_file() or target.stat().st_size <= 0:
            return None
        return row

    def _can_retry_failed(self, observed: ObservedCandidateRow, candidate: CandidateOut) -> bool:
        public = self.settings.get_public()
        if not public.automatic_retry_enabled:
            return False
        if observed.automatic_attempts >= public.maximum_automatic_retries:
            return False
        latest = self._latest_job(candidate.key, "translate")
        if latest is None:
            return True
        if latest.status in OPEN_JOB_STATUSES:
            return False
        if latest.status == "completed":
            # Completed with verify warning may get a verify-only retry from scanner.
            if latest.reason_code == "bazarr_verify_failed":
                target = Path(latest.target_subtitle_path)
                if target.is_file() and target.stat().st_size > 0:
                    return False  # do not re-translate; verify handled separately
            return False
        if latest.status in {"skipped", "cancelled"}:
            reason = latest.reason_code or ""
            if reason in NON_RETRYABLE_REASON_CODES:
                return False
            return True
        if latest.status == "failed":
            reason = latest.reason_code or "failed"
            if reason in NON_RETRYABLE_REASON_CODES:
                return False
            if reason == "openrouter_auth":
                return False
            return reason in RETRYABLE_REASON_CODES or reason == "failed"
        return False

    def next_action(
        self,
        candidate: CandidateOut,
        observed: ObservedCandidateRow,
        *,
        now: datetime | None = None,
    ) -> Literal["none", "translate", "extract", "request", "wait_grace", "verify"]:
        now = now or utcnow()
        public = self.settings.get_public()

        if self._active_job(candidate.key) is not None:
            return "none"

        if not self.grace_expired(observed, public.bazarr_grace_period_minutes, now=now):
            return "wait_grace"

        # Written target + Bazarr still missing → verify/rescan only, never translate again.
        if self._latest_verify_failed_translate(candidate.key) is not None:
            return "verify"

        if candidate.reason_code == "target_exists":
            return "none"

        # Avoid immediately redoing successful automatic translate.
        latest_translate = self._latest_job(candidate.key, "translate")
        if latest_translate and latest_translate.status == "completed":
            target = Path(latest_translate.target_subtitle_path)
            if target.is_file() and target.stat().st_size > 0:
                return "none"

        if candidate.can_translate:
            if latest_translate and latest_translate.status in {"failed", "cancelled", "skipped"}:
                if not self._can_retry_failed(observed, candidate):
                    return "none"
            return "translate"

        if candidate.can_extract:
            if candidate.active_extract_job_id is not None:
                return "none"
            return "extract"

        # Request source via Bazarr when IDs exist.
        from app.jobs.service import JobService

        if JobService._can_request_source(candidate):
            if candidate.active_request_job_id is not None:
                return "none"
            cooldown = JobService(self.db)._recent_not_found_cooldown(candidate.key)
            if cooldown is not None:
                return "none"
            latest_request = self._latest_job(candidate.key, "request")
            if latest_request and latest_request.status == "completed":
                # TaskPlanner translates from a readable source; do not enqueue another search.
                return "none"
            return "request"

        return "none"

    async def enqueue_for_candidate(
        self,
        candidate: CandidateOut,
        *,
        now: datetime | None = None,
    ) -> tuple[JobOut | None, str]:
        """Ensure a LocalizationTask and plan the next execution.

        Grace period, retry cooldown, and next_action gates remain intact.
        """
        from app.jobs.service import JobService
        from app.languages import normalize_language
        from app.localization.planner import TaskPlanner
        from app.localization.service import LocalizationTaskService
        from app.media.service import MediaItemService

        now = now or utcnow()
        if not self.settings.is_automatic_fallback_enabled():
            return None, "disabled"

        observed = self.observe_candidate(candidate, now=now)
        action = self.next_action(candidate, observed, now=now)

        if action == "wait_grace":
            # Still ensure a task exists so UI shows waiting, but do not enqueue jobs.
            try:
                media = MediaItemService(self.db).upsert_from_candidate_fields(
                    media_type=candidate.media_type,
                    title=candidate.title,
                    path=candidate.media_path,
                    bazarr_movie_id=candidate.bazarr_movie_id,
                    bazarr_series_id=candidate.bazarr_series_id,
                    bazarr_episode_id=candidate.bazarr_episode_id,
                )
                language = normalize_language(candidate.target_language)
                task_svc = LocalizationTaskService(self.db)
                task, _ = task_svc.ensure_task(
                    media_item=media,
                    language=language,
                    capability="subtitles",
                    origin="automatic",
                )
                task = task_svc.get(task.id)
                if task and task.status in {"requested", "planning", "waiting_for_source"}:
                    task.substate = "grace_period"
                    self.db.add(task)
                    self.db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Grace-period task ensure failed: %s", exc)
            observed.last_outcome = "wait_grace"
            self.db.add(observed)
            self.db.commit()
            return None, "wait_grace"
        if action == "none":
            # Target exists / nothing to do — complete active, failed, or blocked tasks.
            if candidate.reason_code == "target_exists":
                try:
                    media = MediaItemService(self.db).upsert_from_candidate_fields(
                        media_type=candidate.media_type,
                        title=candidate.title,
                        path=candidate.media_path,
                        bazarr_movie_id=candidate.bazarr_movie_id,
                        bazarr_series_id=candidate.bazarr_series_id,
                        bazarr_episode_id=candidate.bazarr_episode_id,
                    )
                    language = normalize_language(candidate.target_language)
                    task_svc = LocalizationTaskService(self.db)
                    planner = TaskPlanner(self.db)
                    for task in task_svc.list_unresolved_for_media(
                        media.id, language.code, "subtitles"
                    ):
                        await planner.plan(task.id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Target-exists reconcile failed: %s", exc)
            observed.last_outcome = "none"
            if candidate.reason_code:
                observed.last_reason_code = candidate.reason_code
            self.db.add(observed)
            self.db.commit()
            return None, "none"

        # Ensure LocalizationTask, then enqueue via existing job paths (preserves
        # grace/retry/verify semantics) and attach task_id.
        try:
            media = MediaItemService(self.db).upsert_from_candidate_fields(
                media_type=candidate.media_type,
                title=candidate.title,
                path=candidate.media_path,
                bazarr_movie_id=candidate.bazarr_movie_id,
                bazarr_series_id=candidate.bazarr_series_id,
                bazarr_episode_id=candidate.bazarr_episode_id,
            )
            language = normalize_language(candidate.target_language)
            task_svc = LocalizationTaskService(self.db)
            task, _ = task_svc.ensure_task(
                media_item=media,
                language=language,
                capability="subtitles",
                origin="automatic",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Automatic task ensure failed: %s", exc)
            task = None

        jobs = JobService(self.db)
        task_id = task.id if task is not None else None

        if action == "verify":
            latest = self._latest_verify_failed_translate(candidate.key)
            if latest is None:
                observed.last_outcome = "none"
                self.db.add(observed)
                self.db.commit()
                return None, "none"
            if task_id is not None and not getattr(latest, "task_id", None):
                latest.task_id = task_id
                self.db.add(latest)
                self.db.commit()
            if task_id is not None:
                # Task-backed verify retries are owned by TaskPlanner.
                from app.jobs.service import job_to_out

                await TaskPlanner(self.db).plan(task_id)
                self.db.refresh(latest)
                job = job_to_out(latest)
            else:
                # Legacy path for jobs that are not task-backed.
                job = await jobs.retry_bazarr_sync(latest.id)
            observed.last_outcome = "verify"
            observed.last_reason_code = job.reason_code
            observed.last_automatic_attempt_at = now
            self.db.add(observed)
            self.db.commit()
            logger.info(
                "Automatic verify-only retry job_id=%s candidate=%s reason=%s",
                job.id,
                candidate.title,
                job.reason_code,
            )
            return job, "verify"

        try:
            if action == "translate":
                existing = self._active_job(candidate.key, "translate")
                job = await jobs.create_job(
                    JobCreate(candidate_key=candidate.key),
                    candidate=candidate,
                    trigger_type="automatic",
                    task_id=task_id,
                )
                reused = existing is not None and existing.id == job.id
            elif action == "extract":
                existing = self._active_job(candidate.key, "extract")
                job = await jobs.create_extract_job(
                    ExtractCreate(candidate_key=candidate.key),
                    candidate=candidate,
                    trigger_type="automatic",
                    task_id=task_id,
                )
                reused = existing is not None and existing.id == job.id
            else:
                existing = self._active_job(candidate.key, "request")
                job = await jobs.create_request_subtitle_job(
                    candidate.key,
                    candidate=candidate,
                    trigger_type="automatic",
                    task_id=task_id,
                )
                reused = existing is not None and existing.id == job.id
        except (ValueError, AIProviderError, BazarrError, EmbeddedError) as exc:
            observed.last_outcome = "error"
            observed.last_reason_code = "enqueue_failed"
            self.db.add(observed)
            self.db.commit()
            raise ValueError(str(exc)) from exc

        if task_id is not None:
            row = self.db.get(JobRow, job.id)
            if row is not None and getattr(row, "task_id", None) != task_id:
                LocalizationTaskService(self.db).attach_job(row, task_id)
            try:
                current = LocalizationTaskService(self.db).get(task_id)
                if current and job.status in OPEN_JOB_STATUSES:
                    if current.status in {"requested", "planning", "waiting_for_source"}:
                        LocalizationTaskService(self.db).transition(
                            current,
                            "processing",
                            substate=(
                                "translating"
                                if action == "translate"
                                else "extracting_source"
                                if action == "extract"
                                else "discovering_source"
                            ),
                            clear_error=True,
                        )
            except Exception:  # noqa: BLE001
                pass

        if job.status in OPEN_JOB_STATUSES and not reused:
            observed.automatic_attempts = int(observed.automatic_attempts or 0) + 1
            observed.last_automatic_attempt_at = now
        observed.last_outcome = action
        observed.last_reason_code = job.reason_code
        self.db.add(observed)
        self.db.commit()
        return job, ("reused" if reused else "created")

    async def reconcile_active_tasks(self) -> int:
        """Re-plan active tasks (target appeared, source available, resume)."""
        from app.localization.planner import TaskPlanner

        return await TaskPlanner(self.db).plan_all_active()

    async def maybe_chain_translate(self, *, candidate_key: str, source_path: str) -> JobOut | None:
        """After automatic extract/request success, enqueue translate if still enabled."""
        from app.jobs.service import JobService

        if not self.settings.is_automatic_fallback_enabled():
            return None
        if self._active_job(candidate_key, "translate") is not None:
            return None

        match = await CandidateService(self.db).get_candidate(candidate_key)
        if match is None:
            # Candidate may have left wanted after request/extract; still try path-based create.
            observed = self.db.get(ObservedCandidateRow, candidate_key)
            if observed is None:
                return None
            if Path(source_path).exists():
                return await JobService(self.db).create_job(
                    JobCreate(
                        candidate_key=candidate_key,
                        source_subtitle_path=source_path,
                        target_language=observed.target_language,
                        media_type=observed.media_type,  # type: ignore[arg-type]
                        media_path=observed.media_path,
                        media_title=observed.media_title,
                        bazarr_movie_id=observed.bazarr_movie_id,
                        bazarr_episode_id=observed.bazarr_episode_id,
                        bazarr_series_id=observed.bazarr_series_id,
                    ),
                    trigger_type="automatic",
                )
            return None

        if match.reason_code == "target_exists":
            return None
        if not match.can_translate and not Path(source_path).exists():
            return None

        payload = JobCreate(candidate_key=match.key)
        if match.source_subtitle_path:
            return await JobService(self.db).create_job(
                payload,
                candidate=match,
                trigger_type="automatic",
            )
        if Path(source_path).exists():
            return await JobService(self.db).create_job(
                JobCreate(
                    candidate_key=match.key,
                    source_subtitle_path=source_path,
                    target_language=match.target_language,
                    media_type=match.media_type,
                    media_path=match.media_path,
                    media_title=match.title,
                    bazarr_movie_id=match.bazarr_movie_id,
                    bazarr_episode_id=match.bazarr_episode_id,
                    bazarr_series_id=match.bazarr_series_id,
                    source_language=match.source_language,
                ),
                trigger_type="automatic",
            )
        return None

    async def scan_once(self) -> AutomationScanResult:
        now = utcnow()
        public = self.settings.get_public()
        if not public.automatic_fallback_enabled:
            return AutomationScanResult(
                ok=False,
                message="Automatic fallback is disabled",
                scanned_at=now,
                enabled=False,
            )

        created_count = 0
        reused_count = 0
        skipped_count = 0
        errors: list[str] = []

        try:
            candidates = await CandidateService(self.db).list_candidates()
        except BazarrError as exc:
            return AutomationScanResult(
                ok=False,
                message=str(exc),
                scanned_at=now,
                enabled=True,
                errors=[str(exc)],
            )

        present_keys = {c.key for c in candidates}
        self.mark_not_wanted(present_keys, now=now)

        # Soft reconcile only: complete tasks whose target already exists.
        # Job enqueue remains exclusively in enqueue_for_candidate (grace/retry).
        try:
            from app.localization.planner import TaskPlanner
            from app.localization.service import LocalizationTaskService
            from app.media.service import MediaItemService

            planner = TaskPlanner(self.db)
            media_svc = MediaItemService(self.db)
            task_svc = LocalizationTaskService(self.db)
            for task in task_svc.list_unresolved():
                media = media_svc.get(task.media_item_id)
                if media is None:
                    continue
                try:
                    if await planner._target_satisfied(media, task.target_language_code):
                        await planner.plan(task.id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Task reconcile failed task=%s error=%s", task.id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Active task reconcile failed: %s", exc)

        for candidate in candidates:
            try:
                job, outcome = await self.enqueue_for_candidate(candidate, now=now)
                if outcome == "created":
                    created_count += 1
                elif outcome in {"reused", "verify"}:
                    reused_count += 1
                else:
                    skipped_count += 1
                if job is None:
                    continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{candidate.title}: {exc}")
                logger.warning("Automatic enqueue failed title=%s error=%s", candidate.title, exc)

        return AutomationScanResult(
            ok=True,
            message=None,
            created_count=created_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            errors=errors,
            scanned_at=now,
            enabled=True,
        )
