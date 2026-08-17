"""Task planner: decide next execution for a LocalizationTask."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CandidateOut, ExtractCreate, JobCreate
from app.core.logging import get_logger
from app.db.models import JobRow, LocalizationTaskRow, MediaItemRow
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.bazarr.paths import apply_path_mapping, mappings_from_settings
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
from app.services.candidates import CandidateService, candidate_key, to_bazarr_code2
from app.services.settings import SettingsService
from app.subtitles.embedded import pick_extractable_track, probe_subtitle_tracks
from app.subtitles.filenames import (
    find_source_srt_beside_media,
    language_matches,
    languages_compatible,
    normalize_language_code,
    subtitle_belongs_to_media,
)

logger = get_logger("task_planner")

MSG_WAITING_SOURCE = "Waiting for a subtitle source."
MSG_RETRY_SOURCE = "No suitable subtitle source found yet. Subtitle AI will retry."
MSG_UNSUPPORTED = "This localization capability is not available."
MSG_MEDIA_MISSING = "This media is no longer available."
MSG_NO_MEDIA_REF = "No usable media reference is available."
MSG_EXTRACT_FAILED = "Source extraction failed."
MSG_TRANSLATE_FAILED = "Translation failed."


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
        if task.status not in ACTIVE_STATUSES and task.status != "planning":
            if task.status in {"failed", "blocked"}:
                return task
            return task
        if task.status == "cancelled":
            return task

        media = self.media.get(task.media_item_id)
        if media is None:
            return self.tasks.transition(
                task,
                "blocked",
                error_code="media_missing",
                error_message=MSG_MEDIA_MISSING,
            )

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
            wrote = (
                latest_written is not None
                and latest_written.status == "completed"
                and Path(latest_written.target_subtitle_path).is_file()
                and Path(latest_written.target_subtitle_path).stat().st_size > 0
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

        # Resolve source / next action via current mechanisms
        snapshot = await self._resolve_source_snapshot(media, task.target_language_code)
        self._prefer_completed_extract(task.id, snapshot)
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
                    JobRow.status.in_(["pending", "processing"]),
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
            if cooldown is not None:
                return self.tasks.transition(
                    task,
                    "waiting_for_source",
                    substate="source_cooldown",
                    error_code="not_found",
                    error_message=MSG_RETRY_SOURCE,
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
                    return self.tasks.transition(
                        task,
                        "waiting_for_source",
                        substate="awaiting_source",
                        error_code="not_found",
                        error_message=MSG_RETRY_SOURCE,
                    )
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

        # Failed request with not_found
        latest_request = self._latest_job(task.id, "request")
        if latest_request and latest_request.reason_code == "not_found":
            return self.tasks.transition(
                task,
                "waiting_for_source",
                substate="awaiting_source",
                error_code="not_found",
                error_message=MSG_RETRY_SOURCE,
            )

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
        return count

    async def on_job_finished(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if row is None or not getattr(row, "task_id", None):
            return
        try:
            await self.plan(int(row.task_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_job_finished job=%s error=%s", job_id, exc)

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
        target = Path(latest_translate.target_subtitle_path)
        if target.is_file() and target.stat().st_size <= 0:
            return False
        if latest_translate.reason_code in {"bazarr_verify_failed", "bazarr_rescan_failed"}:
            return True
        return target.is_file() and target.stat().st_size > 0

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
                return self.tasks.transition(task, "completed", substate=None, clear_error=True)
        self.tasks.update_checkpoints(task.id, verify="failed")
        return self.tasks.transition(
            task,
            "verifying",
            substate="bazarr_sync",
            error_code=result.reason_code or "bazarr_verify_failed",
            error_message=result.message or USER_VERIFY_FAILED,
        )

    def _active_job_for_task(self, task_id: int) -> JobRow | None:
        return self.db.scalar(
            select(JobRow)
            .where(
                JobRow.task_id == task_id,
                JobRow.status.in_(["pending", "processing"]),
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

    def _overlay_disk_source(self, result: dict, path: str, source_langs: list[str]) -> None:
        """Prefer a sidecar SRT on disk over a stale Bazarr wanted-list snapshot."""
        if result.get("can_translate") and result.get("source_path"):
            return
        if not path:
            return
        found = find_source_srt_beside_media(Path(path), source_langs)
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
                from app.subtitles.filenames import build_external_subtitle_path

                direct = build_external_subtitle_path(media_path, target_language)
                if direct.is_file() and direct.stat().st_size <= 0:
                    return False
        return await self._bazarr_target_present(media, target_language)

    async def _bazarr_target_present(self, media: MediaItemRow, target_language: str) -> bool:
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            return False
        client = BazarrClient(bazarr_url, bazarr_key)
        try:
            if media.media_type == "movie" and media.bazarr_movie_id is not None:
                detail = await client.get_movie(media.bazarr_movie_id)
            elif media.bazarr_episode_id is not None:
                detail = await client.get_episode(media.bazarr_episode_id)
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
                self._overlay_disk_source(result, path, source_langs)
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
            from app.subtitles.filenames import build_external_subtitle_path

            target = build_external_subtitle_path(media_path, target_language)
            if target.is_file() and target.stat().st_size > 0:
                result["target_exists"] = True
                return result

            found = find_source_srt_beside_media(media_path, source_langs)
            if found:
                result["can_translate"] = True
                result["source_path"] = str(found[0])
                result["source_language"] = found[1]
                return result

            if media_path.is_file():
                tracks = await probe_subtitle_tracks(str(media_path))
                pick = pick_extractable_track(tracks, source_langs)
                if pick is not None:
                    result["can_extract"] = True
                    result["extract_stream_index"] = pick.stream_index
                    result["source_language"] = pick.language

        # Bazarr request if IDs exist
        if media.media_type == "movie" and media.bazarr_movie_id is not None:
            result["can_request"] = True
        elif media.bazarr_episode_id is not None and media.bazarr_series_id is not None:
            result["can_request"] = True

        # Enrich from Bazarr subtitle metadata
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if bazarr_url and not result["can_translate"]:
            client = BazarrClient(bazarr_url, bazarr_key)
            try:
                if media.media_type == "movie" and media.bazarr_movie_id is not None:
                    detail = await client.get_movie(media.bazarr_movie_id)
                elif media.bazarr_episode_id is not None:
                    detail = await client.get_episode(media.bazarr_episode_id)
                else:
                    detail = None
                if detail:
                    if BazarrClient.target_subtitle_present(detail, target_language):
                        result["target_exists"] = True
                        return result
                    for sub in BazarrClient.parse_subtitles(detail):
                        if not sub.path:
                            continue
                        lang = normalize_language_code(sub.language_code)
                        if lang and language_matches(lang, source_langs):
                            mapped = apply_path_mapping(sub.path, mappings)
                            if mapped.lower().endswith(".srt") and (
                                not path or subtitle_belongs_to_media(mapped, path)
                            ):
                                if Path(mapped).is_file():
                                    result["can_translate"] = True
                                    result["source_path"] = mapped
                                    result["source_language"] = lang
                                    break
            except BazarrError:
                pass

        return result
