"""Task planner: decide next execution for a LocalizationTask."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ExtractCreate, JobCreate
from app.core.logging import get_logger
from app.db import release_session_connection
from app.db.models import JobRow, LocalizationTaskRow, MediaItemRow
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.bazarr.paths import apply_path_mapping, mappings_from_settings
from app.jobs.queue import OPEN_JOB_STATUSES
from app.jobs.service import JobService
from app.localization.checkpoints import (
    mark_existing_target_complete,
    mark_pipeline_ready_for_translate,
    mark_write_complete,
)
from app.localization.service import LocalizationTaskService
from app.localization.state import ACTIVE_STATUSES
from app.localization.verification import (
    USER_RESCAN_FAILED,
    USER_VERIFY_FAILED,
    BazarrVerificationService,
)
from app.media.service import MediaItemService
from app.services.candidates import CandidateService, candidate_key
from app.services.settings import SettingsService
from app.subtitles.embedded import pick_extractable_track, probe_subtitle_tracks
from app.subtitles.filenames import (
    build_dub_preview_path,
    find_existing_sidecar,
    find_source_srt_beside_media,
    is_origin_language,
    languages_compatible,
    origin_language_rank,
    normalize_language_code,
    subtitle_belongs_to_media,
)

logger = get_logger("task_planner")

MSG_WAITING_SOURCE = "Waiting for a subtitle source."
MSG_RETRY_SOURCE = "No suitable subtitle source found yet. Subtitle AI will retry."
MSG_SOURCE_NOT_FOUND = "No suitable subtitle source was found."
MSG_UNSUPPORTED = "This localization capability is not available."
MSG_MEDIA_MISSING = "This media is no longer available."
MSG_NO_MEDIA_REF = "No usable media reference is available."
MSG_EXTRACT_FAILED = "Source extraction failed."
MSG_TRANSCRIBE_FAILED = "Audio transcription failed."
MSG_TRANSLATE_FAILED = "Translation failed."
MSG_DUB_FAILED = "Dubbing failed."
MSG_WAITING_SUBTITLES = "Localize subtitles first."


def _readable_request_source(job: JobRow) -> Path | None:
    """Return a non-empty SRT written by a completed request job, if present on disk."""
    for raw in (job.target_subtitle_path, job.source_subtitle_path):
        if not raw:
            continue
        path = Path(raw)
        if path.suffix.lower() != ".srt":
            continue
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


class TaskPlanner:
    """Owns all progression for task-backed localization work.

    JobService creates, runs, and inspects one execution. When that execution
    finishes, TaskPlanner.on_job_finished() reconciles task state and creates
    the next execution if required.

    Task-backed orchestration belongs exclusively to TaskPlanner.
    JobService._maybe_chain_automatic_translate remains only as a documented
    legacy path for jobs that are not task-backed.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)
        self.tasks = LocalizationTaskService(db)
        self.media = MediaItemService(db)

    async def plan(self, task_id: int) -> LocalizationTaskRow | None:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task.status == "awaiting_approval":
            return task
        if task.status not in ACTIVE_STATUSES and task.status != "planning":
            if task.status in {"failed", "blocked"}:
                media = self.media.get(task.media_item_id)
                if media is None:
                    return task
                if task.capability == "audio":
                    if not self._audio_target_satisfied(media, task.target_language_code):
                        return task
                    self.tasks.update_checkpoints(
                        task.id,
                        source="done",
                        extract="skipped",
                        translate="skipped",
                        validate="skipped",
                        write="done",
                        sync="skipped",
                        verify="done",
                    )
                elif not await self._target_satisfied(media, task.target_language_code):
                    return task
                else:
                    self._clear_verify_failure(task.id)
                    self.tasks.update_checkpoints(task.id, **mark_existing_target_complete())
                return self.tasks.transition(
                    task,
                    "completed",
                    substate=None,
                    clear_error=True,
                )
            return task

        media = self.media.get(task.media_item_id)
        if media is None:
            return self.tasks.transition(
                task,
                "blocked",
                error_code="media_missing",
                error_message=MSG_MEDIA_MISSING,
            )

        if task.capability == "audio":
            return await self._plan_audio(task, media)

        if task.capability != "subtitles":
            return self.tasks.transition(
                task,
                "blocked",
                error_code="unsupported_capability",
                error_message=MSG_UNSUPPORTED,
            )

        if task.status == "requested":
            self.tasks.transition(task, "planning", substate="discovering_source", clear_error=True)
            task = self.tasks.get(task_id)  # type: ignore[assignment]

        assert task is not None

        # Active job for this task → wait
        active = self._active_job_for_task(task.id)
        if active is not None:
            return self._sync_status_from_job(task, active)

        # Target already present → complete without AI (unless we just wrote it)
        if await self._target_satisfied(media, task.target_language_code):
            self._clear_verify_failure(task.id)
            latest_written = self._latest_job(task.id, "translate")
            latest_transcribe = self._latest_job(task.id, "transcribe")
            wrote = (
                latest_written is not None
                and latest_written.status == "completed"
                and find_existing_sidecar(
                    latest_written.target_subtitle_path,
                    latest_written.target_language or task.target_language_code,
                )
                is not None
            )
            transcribed_target = (
                latest_transcribe is not None
                and latest_transcribe.status == "completed"
                and languages_compatible(
                    latest_transcribe.source_language, task.target_language_code
                )
                and find_existing_sidecar(
                    latest_transcribe.target_subtitle_path,
                    latest_transcribe.source_language or task.target_language_code,
                )
                is not None
            )
            if wrote:
                self.tasks.update_checkpoints(
                    task.id,
                    translate="done",
                    validate="done",
                    write="done",
                    sync="done",
                    verify="done",
                )
                await self._discard_extracted_source(
                    task,
                    media,
                    target_path=latest_written.target_subtitle_path,
                )
            elif transcribed_target:
                self.tasks.update_checkpoints(
                    task.id,
                    source="done",
                    extract="skipped",
                    translate="skipped",
                    validate="skipped",
                    write="done",
                    sync="done",
                    verify="done",
                )
            else:
                self.tasks.update_checkpoints(task.id, **mark_existing_target_complete())
            return self.tasks.transition(
                task,
                "completed",
                substate=None,
                clear_error=True,
            )

        # Written translate (or an existing verifying task) → rescan/verify.
        # Must run even when the job has no bazarr_verify_failed reason_code:
        # the worker can mark the job completed before verify finishes, and a
        # later replan used to sit in verifying forever without calling Bazarr.
        latest_translate = self._latest_job(task.id, "translate")
        if self._needs_verify(task, latest_translate):
            return await self._advance_verify(task, media)

        # Failed translate → fail task unless this is an explicit retry (planning)
        if latest_translate and latest_translate.status == "failed":
            if task.status != "planning":
                self.tasks.update_checkpoints(task.id, translate="failed")
                return self.tasks.transition(
                    task,
                    "failed",
                    error_code=latest_translate.reason_code or "failed",
                    error_message=MSG_TRANSLATE_FAILED,
                )

        latest_extract = self._latest_job(task.id, "extract")
        if latest_extract and latest_extract.status == "failed":
            if task.status != "planning":
                self.tasks.update_checkpoints(task.id, extract="failed")
                return self.tasks.transition(
                    task,
                    "failed",
                    error_code=latest_extract.reason_code or "extract_failed",
                    error_message=MSG_EXTRACT_FAILED,
                )

        latest_transcribe = self._latest_job(task.id, "transcribe")
        if latest_transcribe and latest_transcribe.status == "failed":
            if task.status != "planning":
                self.tasks.update_checkpoints(task.id, source="failed")
                return self.tasks.transition(
                    task,
                    "failed",
                    error_code=latest_transcribe.reason_code or "transcribe_failed",
                    error_message=MSG_TRANSCRIBE_FAILED,
                )

        # Resolve source / next action via current mechanisms
        snapshot = await self._resolve_source_snapshot(media, task.target_language_code)
        self._prefer_completed_extract(task.id, snapshot)
        self._prefer_completed_transcribe(task, snapshot)
        self._prefer_completed_request(task, snapshot)
        trigger = "manual" if task.origin == "manual" else "automatic"
        jobs = JobService(self.db)

        if snapshot.get("target_exists"):
            if await self._target_satisfied(media, task.target_language_code):
                return self.tasks.transition(task, "completed", clear_error=True)
            return await self._advance_verify(task, media)

        # Attach any orphan active job for same candidate
        ckey = snapshot.get("candidate_key")
        if ckey:
            orphan = self.db.scalar(
                select(JobRow).where(
                    JobRow.candidate_key == ckey,
                    JobRow.status.in_(OPEN_JOB_STATUSES),
                    JobRow.task_id.is_(None),
                )
            )
            if orphan is not None:
                self.tasks.attach_job(orphan, task.id)
                return self._sync_status_from_job(task, orphan)

        if snapshot.get("can_translate") and snapshot.get("source_path"):
            extracted = self._latest_job(task.id, "extract")
            self.tasks.update_checkpoints(
                task.id,
                **mark_pipeline_ready_for_translate(
                    extracted=bool(extracted and extracted.status == "completed")
                ),
            )
            self.tasks.transition(task, "processing", substate="translating", clear_error=True)
            try:
                job = await jobs.create_job(
                    JobCreate(
                        candidate_key=ckey,
                        source_subtitle_path=snapshot["source_path"],
                        target_language=task.target_language_code,
                        media_type=media.media_type if media.media_type in {"movie", "episode"} else "movie",  # type: ignore[arg-type]
                        media_path=media.path,
                        media_title=media.title,
                        bazarr_movie_id=media.bazarr_movie_id,
                        bazarr_episode_id=media.bazarr_episode_id,
                        bazarr_series_id=media.bazarr_series_id,
                        source_language=snapshot.get("source_language"),
                    ),
                    trigger_type=trigger,
                    task_id=task.id,
                )
                row = self.db.get(JobRow, job.id)
                if row:
                    self.tasks.attach_job(row, task.id)
                if job.status == "skipped" and job.reason_code == "target_exists":
                    if await self._target_satisfied(media, task.target_language_code):
                        return self.tasks.transition(task, "completed", clear_error=True)
                    return await self._advance_verify(task, media)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Translate enqueue failed task=%s error=%s", task.id, exc)
                return self.tasks.transition(
                    task,
                    "failed",
                    error_code="enqueue_failed",
                    error_message=MSG_TRANSLATE_FAILED,
                )
            return self.tasks.get(task.id)

        latest_extract = self._latest_job(task.id, "extract")
        if latest_extract and latest_extract.status == "completed":
            snapshot["can_extract"] = False

        if snapshot.get("can_extract") and snapshot.get("extract_stream_index") is not None and ckey:
            self.tasks.update_checkpoints(task.id, source="done", extract="active")
            self.tasks.transition(task, "processing", substate="extracting_source", clear_error=True)
            try:
                # Prefer candidate-based extract when on wanted list
                job = await jobs.create_extract_job(
                    ExtractCreate(candidate_key=ckey),
                    trigger_type=trigger,
                    task_id=task.id,
                )
                row = self.db.get(JobRow, job.id)
                if row:
                    self.tasks.attach_job(row, task.id)
            except Exception:
                # Fall through to request if extract enqueue fails for non-wanted media
                pass
            else:
                return self.tasks.get(task.id)

        if snapshot.get("can_request"):
            cooldown = JobService(self.db)._recent_not_found_cooldown(ckey) if ckey else None
            if cooldown is not None and task.status != "planning":
                return self._after_source_not_found(
                    task,
                    substate="source_cooldown",
                    waiting_message=MSG_RETRY_SOURCE,
                )
            self.tasks.update_checkpoints(task.id, source="active")
            self.tasks.transition(task, "processing", substate="discovering_source", clear_error=True)
            try:
                if ckey:
                    job = await jobs.create_request_subtitle_job(
                        ckey,
                        trigger_type=trigger,
                        task_id=task.id,
                    )
                else:
                    job = await jobs.create_request_subtitle_job_for_media(
                        media_type="movie" if media.media_type == "movie" else "episode",
                        media_path=media.path or "",
                        media_title=media.title,
                        bazarr_movie_id=media.bazarr_movie_id,
                        bazarr_episode_id=media.bazarr_episode_id,
                        bazarr_series_id=media.bazarr_series_id,
                        target_language=task.target_language_code,
                        trigger_type=trigger,
                        task_id=task.id,
                    )
                row = self.db.get(JobRow, job.id)
                if row:
                    self.tasks.attach_job(row, task.id)
                if job.status == "skipped" and job.reason_code == "not_found":
                    return self._after_source_not_found(task)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Source request failed task=%s error=%s", task.id, exc)
                return self.tasks.transition(
                    task,
                    "waiting_for_source",
                    substate="awaiting_source",
                    error_code="source_unavailable",
                    error_message=MSG_WAITING_SOURCE,
                )
            return self.tasks.get(task.id)

        # Failed request with not_found, or a "success" that produced no readable SRT.
        latest_request = self._latest_job(task.id, "request")
        if latest_request and latest_request.reason_code == "not_found":
            return self._after_source_not_found(task)
        if (
            latest_request
            and latest_request.status == "completed"
            and not snapshot.get("can_translate")
            and not snapshot.get("can_request")
        ):
            return self._after_source_not_found(task)

        if not media.path and media.bazarr_movie_id is None and media.bazarr_episode_id is None:
            return self.tasks.transition(
                task,
                "blocked",
                error_code="no_media_ref",
                error_message=MSG_NO_MEDIA_REF,
            )

        return self.tasks.transition(
            task,
            "waiting_for_source",
            substate="awaiting_source",
            error_code="source_unavailable",
            error_message=MSG_WAITING_SOURCE,
        )

    async def plan_all_active(self) -> int:
        count = 0
        for task in self.tasks.list_active():
            try:
                await self.plan(task.id)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("plan_all_active task=%s error=%s", task.id, exc)
            finally:
                release_session_connection(self.db)
        return count

    async def on_job_finished(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if row is None or not getattr(row, "task_id", None):
            return
        try:
            await self.plan(int(row.task_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_job_finished job=%s error=%s", job_id, exc)

    def _after_source_not_found(
        self,
        task: LocalizationTaskRow,
        *,
        error_code: str = "not_found",
        substate: str = "awaiting_source",
        waiting_message: str = MSG_RETRY_SOURCE,
    ) -> LocalizationTaskRow:
        """Fail a finished manual search; automatic tasks keep waiting for a later source."""
        self.tasks.update_checkpoints(task.id, source="failed")
        if task.origin == "manual" and task.status != "planning":
            return self.tasks.transition(
                task,
                "failed",
                error_code=error_code,
                error_message=MSG_SOURCE_NOT_FOUND,
            )
        return self.tasks.transition(
            task,
            "waiting_for_source",
            substate=substate,
            error_code=error_code,
            error_message=waiting_message,
        )

    def _clear_verify_failure(self, task_id: int) -> None:
        """Drop stale verify-failed markers once the target is actually present."""
        row = self._latest_job(task_id, "translate")
        if row is None or row.reason_code not in {"bazarr_verify_failed", "bazarr_rescan_failed"}:
            return
        row.warning = None
        row.reason_code = None
        self.db.add(row)
        self.db.commit()

    def _needs_verify(self, task: LocalizationTaskRow, latest_translate: JobRow | None) -> bool:
        if task.status == "verifying":
            return True
        if latest_translate is None or latest_translate.status != "completed":
            return False
        if latest_translate.reason_code == "awaiting_approval":
            return False
        existing = find_existing_sidecar(
            latest_translate.target_subtitle_path,
            latest_translate.target_language or task.target_language_code,
        )
        if existing is None:
            target = Path(latest_translate.target_subtitle_path)
            if target.is_file() and target.stat().st_size <= 0:
                return False
            return latest_translate.reason_code in {"bazarr_verify_failed", "bazarr_rescan_failed"}
        return True

    async def _advance_verify(
        self,
        task: LocalizationTaskRow,
        media: MediaItemRow,
    ) -> LocalizationTaskRow:
        """Rescan Bazarr and complete or stay in verifying with a visible error."""
        if task.status != "verifying":
            self.tasks.transition(task, "verifying", substate="bazarr_sync")
            task = self.tasks.get(task.id)  # type: ignore[assignment]
            assert task is not None
        self.tasks.update_checkpoints(task.id, **mark_write_complete())
        try:
            result = await BazarrVerificationService(self.db).rescan_and_verify(
                media, task.target_language_code
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Verify retry failed task=%s error=%s", task.id, exc)
            self.tasks.update_checkpoints(task.id, verify="failed")
            return self.tasks.transition(
                task,
                "verifying",
                substate="bazarr_sync",
                error_code="bazarr_rescan_failed",
                error_message=USER_RESCAN_FAILED,
            )
        if result.ok:
            self._clear_verify_failure(task.id)
            self.tasks.update_checkpoints(task.id, sync="done", verify="done")
            if await self._target_satisfied(media, task.target_language_code):
                latest_written = self._latest_job(task.id, "translate")
                await self._discard_extracted_source(
                    task,
                    media,
                    target_path=latest_written.target_subtitle_path if latest_written else None,
                )
                return self.tasks.transition(task, "completed", substate=None, clear_error=True)
        self.tasks.update_checkpoints(task.id, verify="failed")
        return self.tasks.transition(
            task,
            "verifying",
            substate="bazarr_sync",
            error_code=result.reason_code or "bazarr_verify_failed",
            error_message=result.message or USER_VERIFY_FAILED,
        )

    async def _discard_extracted_source(
        self,
        task: LocalizationTaskRow,
        media: MediaItemRow,
        *,
        target_path: str | None,
    ) -> None:
        """Remove an extracted source sidecar after the target is verified in Bazarr."""
        from app.localization.extracted_source import (
            resolve_extracted_source_path,
            unlink_extracted_source,
        )

        path = resolve_extracted_source_path(self.db, task_id=task.id)
        if path is None:
            return
        public = self.settings.get_public()
        deleted = unlink_extracted_source(
            path,
            target_path=target_path,
            media_path=media.path,
            media_roots=public.media_roots,
        )
        if not deleted:
            return
        try:
            await BazarrVerificationService(self.db).rescan(media)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Bazarr rescan after extracted-source cleanup failed task=%s error=%s",
                task.id,
                exc,
            )

    async def _plan_audio(
        self,
        task: LocalizationTaskRow,
        media: MediaItemRow,
    ) -> LocalizationTaskRow:
        """Audio localization: dub preview sidecar only (no Bazarr verify)."""
        if task.status == "requested":
            self.tasks.transition(task, "planning", substate="dubbing", clear_error=True)
            task = self.tasks.get(task.id)  # type: ignore[assignment]
        assert task is not None

        active = self._active_job_for_task(task.id)
        if active is not None:
            return self._sync_status_from_job(task, active)

        if self._audio_target_satisfied(media, task.target_language_code):
            self.tasks.update_checkpoints(
                task.id,
                source="done",
                extract="skipped",
                translate="skipped",
                validate="skipped",
                write="done",
                sync="skipped",
                verify="done",
            )
            return self.tasks.transition(task, "completed", substate=None, clear_error=True)

        latest_dub = self._latest_job(task.id, "dub")
        if latest_dub and latest_dub.status == "failed":
            if task.status != "planning":
                self.tasks.update_checkpoints(task.id, write="failed")
                return self.tasks.transition(
                    task,
                    "failed",
                    error_code=latest_dub.reason_code or "dub_failed",
                    error_message=MSG_DUB_FAILED,
                )

        if not self._subtitle_source_for_dub(media, task.target_language_code):
            return self.tasks.transition(
                task,
                "blocked",
                substate="awaiting_subtitles",
                error_code="subtitle_missing",
                error_message=MSG_WAITING_SUBTITLES,
            )

        # Manual dub jobs are created by POST /media/{id}/dub; planner waits for work.
        if task.status in {"planning", "requested"}:
            return self.tasks.transition(
                task,
                "processing",
                substate="dubbing",
                clear_error=True,
            )
        return task

    def _local_media_path(self, media: MediaItemRow) -> Path | None:
        if not media.path:
            return None
        public = self.settings.get_public()
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        return Path(apply_path_mapping(media.path, mappings))

    def _subtitle_source_for_dub(self, media: MediaItemRow, target_language: str) -> bool:
        media_path = self._local_media_path(media)
        if media_path is None or not media_path.exists():
            return False
        return find_existing_sidecar(media_path, target_language) is not None

    def _audio_target_satisfied(self, media: MediaItemRow, target_language: str) -> bool:
        media_path = self._local_media_path(media)
        if media_path is None or not media_path.exists():
            return False
        output = build_dub_preview_path(media_path, target_language)
        return output.is_file() and output.stat().st_size > 0

    def _active_job_for_task(self, task_id: int) -> JobRow | None:
        return self.db.scalar(
            select(JobRow)
            .where(
                JobRow.task_id == task_id,
                JobRow.status.in_(OPEN_JOB_STATUSES),
            )
            .order_by(JobRow.created_at.desc())
            .limit(1)
        )

    def _prefer_completed_extract(self, task_id: int, snapshot: dict) -> None:
        """Use a finished extract as the translation source even if Bazarr is stale."""
        latest_extract = self._latest_job(task_id, "extract")
        if latest_extract is None or latest_extract.status != "completed":
            return
        extract_out = Path(latest_extract.target_subtitle_path)
        if extract_out.is_file() and extract_out.stat().st_size > 0:
            snapshot["can_translate"] = True
            snapshot["source_path"] = str(extract_out)
            if latest_extract.source_language:
                snapshot["source_language"] = latest_extract.source_language
            snapshot["can_extract"] = False
            return
        snapshot["can_extract"] = False

    def _prefer_completed_request(self, task: LocalizationTaskRow, snapshot: dict) -> None:
        """Use a finished Bazarr request as the translation source, and never re-request it.

        Request jobs can complete from Bazarr metadata while the wanted-list snapshot still
        reports no source. Without this, plan() enqueues another request immediately.
        """
        latest = self._latest_job(task.id, "request")
        if latest is None or latest.status != "completed":
            return
        source = _readable_request_source(latest)
        if source is not None:
            snapshot["can_translate"] = True
            snapshot["source_path"] = str(source)
            if latest.source_language:
                snapshot["source_language"] = latest.source_language
            snapshot["can_extract"] = False
            snapshot["can_request"] = False
            return
        if snapshot.get("can_translate") and snapshot.get("source_path"):
            snapshot["can_request"] = False
            return
        # Explicit retry (planning) may search again; otherwise stop the request loop.
        if task.status != "planning":
            snapshot["can_request"] = False

    def _prefer_completed_transcribe(self, task: LocalizationTaskRow, snapshot: dict) -> None:
        """Use a finished transcription as the translation source (or the target itself)."""
        latest = self._latest_job(task.id, "transcribe")
        if latest is None or latest.status != "completed":
            return
        output = Path(latest.target_subtitle_path)
        if not (output.is_file() and output.stat().st_size > 0):
            return
        snapshot["can_extract"] = False
        detected = latest.source_language or ""
        if languages_compatible(detected, task.target_language_code):
            snapshot["target_exists"] = True
            return
        snapshot["can_translate"] = True
        snapshot["source_path"] = str(output)
        snapshot["source_language"] = detected or snapshot.get("source_language")

    def _overlay_disk_source(
        self, result: dict, path: str, source_langs: list[str], target_language: str
    ) -> None:
        """Prefer a sidecar SRT on disk over a stale Bazarr wanted-list snapshot."""
        if result.get("can_translate") and result.get("source_path"):
            return
        if not path:
            return
        found = find_source_srt_beside_media(
            Path(path), source_langs, target_language=target_language
        )
        if not found:
            return
        result["can_translate"] = True
        result["source_path"] = str(found[0])
        result["source_language"] = found[1]

    def _latest_job(self, task_id: int, job_kind: str) -> JobRow | None:
        return self.db.scalar(
            select(JobRow)
            .where(JobRow.task_id == task_id, JobRow.job_kind == job_kind)
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
            .limit(1)
        )

    def _sync_status_from_job(self, task: LocalizationTaskRow, job: JobRow) -> LocalizationTaskRow:
        kind = job.job_kind or "translate"
        if kind == "translate":
            sub = "translating"
        elif kind == "extract":
            sub = "extracting_source"
        elif kind == "request":
            sub = "discovering_source"
        elif kind == "transcribe":
            sub = "transcribing_source"
        elif kind == "dub":
            sub = "dubbing"
        else:
            sub = kind
        if task.status in ACTIVE_STATUSES or task.status == "planning":
            try:
                return self.tasks.transition(task, "processing", substate=sub, clear_error=True)
            except Exception:
                return task
        return task

    async def _target_satisfied(self, media: MediaItemRow, target_language: str) -> bool:
        """Complete only when Bazarr verification succeeds (and any local file is non-empty)."""
        if media.path:
            public = self.settings.get_public()
            mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
            media_path = Path(apply_path_mapping(media.path, mappings))
            if media_path.exists():
                empty = find_existing_sidecar(media_path, target_language)
                if empty is None:
                    from app.subtitles.filenames import build_external_subtitle_path

                    direct = build_external_subtitle_path(media_path, target_language)
                    if direct.is_file() and direct.stat().st_size <= 0:
                        return False
        return await self._bazarr_target_present(media, target_language)

    async def _bazarr_target_present(self, media: MediaItemRow, target_language: str) -> bool:
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            return False
        media_type = media.media_type
        movie_id = media.bazarr_movie_id
        episode_id = media.bazarr_episode_id
        release_session_connection(self.db)
        client = BazarrClient(bazarr_url, bazarr_key)
        try:
            if media_type == "movie" and movie_id is not None:
                detail = await client.get_movie(movie_id)
            elif episode_id is not None:
                detail = await client.get_episode(episode_id)
            else:
                return False
            return BazarrClient.target_subtitle_present(detail, target_language)
        except BazarrError:
            return False

    async def _resolve_source_snapshot(
        self,
        media: MediaItemRow,
        target_language: str,
    ) -> dict:
        """Reuse CandidateService when possible; otherwise probe media directly."""
        public = self.settings.get_public()
        source_langs = public.source_languages or ["en"]
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        path = apply_path_mapping(media.path, mappings) if media.path else ""
        ckey = candidate_key(
            "movie" if media.media_type == "movie" else "episode",
            path or media.external_id,
            target_language,
        ) if path or media.external_id else None

        # Try live candidate list match
        try:
            candidates = await CandidateService(self.db).list_candidates()
            for cand in candidates:
                movie_match = (
                    media.media_type == "movie"
                    and media.bazarr_movie_id is not None
                    and cand.bazarr_movie_id == media.bazarr_movie_id
                    and languages_compatible(cand.target_language, target_language)
                )
                episode_match = (
                    media.bazarr_episode_id is not None
                    and cand.bazarr_episode_id == media.bazarr_episode_id
                    and languages_compatible(cand.target_language, target_language)
                )
                if not movie_match and not episode_match:
                    continue
                result = {
                    "candidate_key": cand.key,
                    "can_translate": cand.can_translate,
                    "can_extract": cand.can_extract,
                    "can_request": JobService._can_request_source(cand),
                    "source_path": cand.source_subtitle_path,
                    "source_language": cand.source_language,
                    "extract_stream_index": cand.extract_stream_index,
                    "target_exists": cand.reason_code == "target_exists",
                }
                self._overlay_disk_source(result, path, source_langs, target_language)
                return result
        except BazarrError:
            pass

        # Direct resolution for on-demand media not on wanted list
        result: dict = {
            "candidate_key": ckey,
            "can_translate": False,
            "can_extract": False,
            "can_request": False,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
        }
        if path:
            media_path = Path(path)
            existing = find_existing_sidecar(media_path, target_language)
            if existing is not None:
                result["target_exists"] = True
                return result

            found = find_source_srt_beside_media(
                media_path, source_langs, target_language=target_language
            )
            if found:
                result["can_translate"] = True
                result["source_path"] = str(found[0])
                result["source_language"] = found[1]
                return result

            if media_path.is_file():
                probe_path = str(media_path)
                release_session_connection(self.db)
                tracks = await probe_subtitle_tracks(probe_path)
                pick = pick_extractable_track(
                    tracks, source_langs, target_language=target_language
                )
                if pick is not None:
                    result["can_extract"] = True
                    result["extract_stream_index"] = pick.stream_index
                    result["source_language"] = pick.language

        media_type = media.media_type
        movie_id = media.bazarr_movie_id
        episode_id = media.bazarr_episode_id
        series_id = media.bazarr_series_id
        # Bazarr request only when nothing local can be used as origin.
        if not result["can_extract"]:
            if media_type == "movie" and movie_id is not None:
                result["can_request"] = True
            elif episode_id is not None and series_id is not None:
                result["can_request"] = True

        # Enrich from Bazarr subtitle metadata
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if bazarr_url and not result["can_translate"]:
            release_session_connection(self.db)
            client = BazarrClient(bazarr_url, bazarr_key)
            try:
                if media_type == "movie" and movie_id is not None:
                    detail = await client.get_movie(movie_id)
                elif episode_id is not None:
                    detail = await client.get_episode(episode_id)
                else:
                    detail = None
                if detail:
                    if BazarrClient.target_subtitle_present(detail, target_language):
                        result["target_exists"] = True
                        return result
                    bazarr_origins: list[tuple[int, str, str]] = []
                    for sub in BazarrClient.parse_subtitles(detail):
                        if not sub.path:
                            continue
                        lang = normalize_language_code(sub.language_code)
                        if not is_origin_language(
                            lang,
                            preferred_languages=source_langs,
                            target_language=target_language,
                            allow_unlabeled=False,
                        ):
                            continue
                        mapped = apply_path_mapping(sub.path, mappings)
                        if mapped.lower().endswith(".srt") and (
                            not path or subtitle_belongs_to_media(mapped, path)
                        ):
                            if Path(mapped).is_file():
                                bazarr_origins.append(
                                    (
                                        origin_language_rank(lang, source_langs),
                                        mapped,
                                        lang or "und",
                                    )
                                )
                    if bazarr_origins:
                        bazarr_origins.sort(key=lambda item: (item[0], item[1]))
                        result["can_translate"] = True
                        result["source_path"] = bazarr_origins[0][1]
                        result["source_language"] = bazarr_origins[0][2]
            except BazarrError:
                pass

        return result
