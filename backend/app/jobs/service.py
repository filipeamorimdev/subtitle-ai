"""Job service and worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.schemas import (
    BatchJobsOut,
    CandidateOut,
    ClearDataResult,
    ExtractCreate,
    JobActionOut,
    JobCreate,
    JobLogOut,
    JobOut,
    JobRequestLogOut,
    JobUsageActionOut,
    JobUsageExchangeOut,
    JobUsageModelOut,
    JobUsageOut,
    JobUsageRelatedOut,
    JobUsageTotalsOut,
    StatsOut,
)
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.db.models import (
    AiRoutingEventRow,
    AiUsageRecordRow,
    JobRow,
    LocalizationTaskRow,
    MediaItemRow,
    TranslationCacheRow,
)
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.bazarr.paths import (
    PathMapping,
    apply_path_mapping,
    is_under_roots,
    mappings_from_settings,
)
from app.ai.bootstrap import bootstrap_providers
from app.ai.errors import AIProviderError, user_message_for_provider_error
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.providers.registry import get_provider_registry
from app.services.ai_budget import AiBudgetService, BudgetBlockedError
from app.services.ai_usage import AiUsageService, RecordingAIProvider, job_stats_action_label
from app.services.candidates import CandidateService, candidate_key, to_bazarr_code2
from app.services.model_router import (
    ModelRouter,
    RoutingBlockedError,
    classify_provider_failure,
    is_technical_failure,
)
from app.services.settings import SettingsService
from app.subtitles.embedded import (
    EmbeddedError,
    extract_embedded_track,
    pick_extractable_track,
    probe_subtitle_tracks,
)
from app.subtitles.filenames import (
    LANG_SUFFIX_RE,
    build_external_subtitle_path,
    build_target_subtitle_path,
    ensure_canonical_sidecar,
    find_source_srt_beside_media,
    language_matches,
    normalize_language_code,
)
from app.subtitles.parsers.srt import parse_srt
from app.subtitles.validation import validate_source
from app.subtitles.writer.srt import write_srt_atomic
from app.jobs.usage import aggregate_usage, parse_exchanges
from app.services.ai_cost import effective_cost_micro, micro_price_to_per_million, micro_to_usd
from app.translation.openrouter.exchange_log import JobOpenRouterExchangeLog, job_openrouter_log_path
from app.translation.service import (
    RetryableTranslationError,
    TranslationCheckpoint,
    TranslationService,
)

# Transitional aliases for legacy imports. Core v0.3 code uses the generic names.
OpenRouterError = AIProviderError
classify_openrouter_failure = classify_provider_failure
RecordingOpenRouterClient = RecordingAIProvider
OpenRouterTranslationService = TranslationService

logger = get_logger("jobs")

REQUEST_POLL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0
REQUEST_HI_RETRY_AFTER_SECONDS = 20.0
REQUEST_NOT_FOUND_COOLDOWN_HOURS = 24


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dedupe_key(
    source_hash: str,
    target_language: str,
    model: str,
    *,
    provider_id: str | None = None,
) -> str:
    raw = f"{source_hash}|{target_language}|{provider_id or ''}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pending_dedupe_key(source_hash: str, target_language: str) -> str:
    """Provider-neutral identity used until ModelRouter selects a candidate."""
    raw = f"{source_hash}|{target_language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def job_to_out(row: JobRow) -> JobOut:
    return JobOut(
        id=row.id,
        candidate_key=row.candidate_key,
        task_id=getattr(row, "task_id", None),
        job_kind=getattr(row, "job_kind", None) or "translate",
        trigger_type=getattr(row, "trigger_type", None) or "manual",
        media_type=row.media_type,
        media_path=row.media_path,
        media_title=row.media_title,
        bazarr_movie_id=row.bazarr_movie_id,
        bazarr_episode_id=row.bazarr_episode_id,
        bazarr_series_id=row.bazarr_series_id,
        source_subtitle_path=row.source_subtitle_path,
        target_subtitle_path=row.target_subtitle_path,
        source_language=row.source_language,
        target_language=row.target_language,
        provider_id=getattr(row, "provider_id", None),
        model=row.model,
        status=row.status,
        progress=row.progress,
        progress_detail=row.progress_detail,
        error=row.error,
        warning=row.warning,
        reason_code=row.reason_code,
        extract_stream_index=getattr(row, "extract_stream_index", None),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _bind_task_id(db, row: JobRow, task_id: int | None) -> JobRow:
    """Attach localization task_id to a job when provided."""
    if task_id is None or row is None:
        return row
    if getattr(row, "task_id", None) == task_id:
        return row
    from app.localization.state import ACTIVE_STATUSES

    task = db.get(LocalizationTaskRow, task_id)
    if task is None or task.status not in ACTIVE_STATUSES:
        return row
    row.task_id = task_id
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _action_duration_seconds(row: JobRow, *, now: datetime | None = None) -> float | None:
    """Elapsed seconds for a job action from start until completion (or now if still running)."""
    start = _as_utc(row.started_at) or _as_utc(row.created_at)
    if start is None:
        return None
    if row.completed_at is not None:
        end = _as_utc(row.completed_at)
    elif row.status in {"pending", "processing"}:
        end = now or datetime.now(timezone.utc)
    else:
        # Cancelled/failed/skipped without completed_at — fall back to created/start only if both exist
        end = _as_utc(row.completed_at)
        if end is None:
            return None
    if end is None or end < start:
        return None
    return round((end - start).total_seconds(), 3)


def _action_sort_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _job_row_to_action(
    item: JobRow,
    *,
    current_id: int | None = None,
    now: datetime | None = None,
) -> JobActionOut:
    message = item.error
    if not message and item.progress_detail:
        if item.status in {"pending", "processing", "skipped", "cancelled"}:
            message = item.progress_detail
    return JobActionOut(
        id=item.id,
        action=getattr(item, "job_kind", None) or "translate",
        status=item.status,
        datetime=item.completed_at or item.started_at or item.created_at,
        duration_seconds=_action_duration_seconds(item, now=now),
        message=message,
        current=current_id is not None and item.id == current_id,
        target_language=item.target_language,
        kind="job",
        progress=item.progress,
        progress_detail=item.progress_detail,
    )


class JobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> list[JobOut]:
        query = select(JobRow).order_by(JobRow.created_at.desc())
        if status:
            query = query.where(JobRow.status == status)
        rows = self.db.scalars(query.limit(limit)).all()
        return [job_to_out(row) for row in rows]

    def get_job(self, job_id: int) -> JobOut | None:
        row = self.db.get(JobRow, job_id)
        return job_to_out(row) if row else None

    def list_job_actions(self, job_id: int, limit: int = 200) -> list[JobActionOut] | None:
        """Return every job run for the same episode/media as ``job_id``."""
        row = self.db.get(JobRow, job_id)
        if row is None:
            return None

        query = select(JobRow)
        if row.candidate_key:
            query = query.where(JobRow.candidate_key == row.candidate_key)
        elif row.media_type == "episode" and row.bazarr_episode_id is not None:
            query = query.where(
                JobRow.media_type == "episode",
                JobRow.bazarr_episode_id == row.bazarr_episode_id,
            )
        elif row.media_type == "movie" and row.bazarr_movie_id is not None:
            query = query.where(
                JobRow.media_type == "movie",
                JobRow.bazarr_movie_id == row.bazarr_movie_id,
            )
        else:
            query = query.where(JobRow.media_path == row.media_path)

        related = self.db.scalars(
            query.order_by(JobRow.created_at.desc(), JobRow.id.desc()).limit(limit)
        ).all()

        now = datetime.now(timezone.utc)
        return [_job_row_to_action(item, current_id=job_id, now=now) for item in related]

    def list_job_actions_for_media(self, media: MediaItemRow, limit: int = 200) -> list[JobActionOut]:
        """Return every job run for a media file (tasks + legacy jobs)."""
        clauses = [
            JobRow.task_id.in_(
                select(LocalizationTaskRow.id).where(LocalizationTaskRow.media_item_id == media.id)
            )
        ]
        if media.media_type == "episode" and media.bazarr_episode_id is not None:
            clauses.append(
                (JobRow.media_type == "episode") & (JobRow.bazarr_episode_id == media.bazarr_episode_id)
            )
        elif media.media_type == "movie" and media.bazarr_movie_id is not None:
            clauses.append(
                (JobRow.media_type == "movie") & (JobRow.bazarr_movie_id == media.bazarr_movie_id)
            )
        if media.path:
            clauses.append(JobRow.media_path == media.path)

        related = self.db.scalars(
            select(JobRow)
            .where(or_(*clauses))
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
            .limit(limit)
        ).all()
        now = datetime.now(timezone.utc)
        actions = [_job_row_to_action(item, now=now) for item in related]
        job_task_ids = {item.task_id for item in related if item.task_id is not None}
        cancelled_job_task_ids = {
            item.task_id
            for item in related
            if item.task_id is not None and item.status == "cancelled"
        }
        from app.localization.state import ACTIVE_STATUSES

        tasks = self.db.scalars(
            select(LocalizationTaskRow)
            .where(LocalizationTaskRow.media_item_id == media.id)
            .order_by(LocalizationTaskRow.created_at.desc(), LocalizationTaskRow.id.desc())
        ).all()
        for task in tasks:
            has_jobs = task.id in job_task_ids
            if has_jobs and task.status not in ACTIVE_STATUSES:
                # Keep cancelled tasks visible when no execution was marked cancelled
                # (e.g. cancelled during verify after translate already completed).
                if not (task.status == "cancelled" and task.id not in cancelled_job_task_ids):
                    continue
            actions.append(
                JobActionOut(
                    id=task.id,
                    action="localize",
                    status=task.status,
                    datetime=task.completed_at or task.started_at or task.created_at,
                    duration_seconds=None,
                    message=task.error_message or task.substate,
                    current=task.status in ACTIVE_STATUSES,
                    target_language=task.target_language_code,
                    kind="task",
                )
            )
        actions.sort(key=lambda row: (_action_sort_datetime(row.datetime), row.id), reverse=True)
        return actions[:limit]

    def get_job_log(self, job_id: int) -> JobLogOut | None:
        row = self.db.get(JobRow, job_id)
        if not row:
            return None
        path = job_openrouter_log_path(get_app_config().config_dir, job_id)
        if not path.is_file():
            return JobLogOut(job_id=job_id, exists=False, path=str(path))
        content = path.read_text(encoding="utf-8")
        entries: list[dict] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                entries.append({"event": "raw", "line": line})
            else:
                if isinstance(parsed, dict):
                    entries.append(parsed)
                else:
                    entries.append({"event": "raw", "value": parsed})
        return JobLogOut(
            job_id=job_id,
            exists=True,
            path=str(path),
            entry_count=len(entries),
            content=content,
            entries=entries,
        )

    def list_job_requests(self, job_id: int) -> list[JobUsageExchangeOut] | None:
        """Return one summary row per OpenRouter exchange in the job log."""
        row = self.db.get(JobRow, job_id)
        if row is None:
            return None
        log = self.get_job_log(job_id)
        assert log is not None
        if not log.exists:
            return []
        exchanges = parse_exchanges(
            log.entries or [],
            fallback_model=row.model,
            pricing_by_model={},
        )
        return [
            JobUsageExchangeOut(
                index=item["index"],
                ts=item.get("ts") if isinstance(item.get("ts"), str) else None,
                model=item["model"],
                action=item["action"],
                attempt=item.get("attempt"),
                input_tokens=item["input_tokens"],
                output_tokens=item["output_tokens"],
                total_tokens=item["total_tokens"],
                cost_usd=item.get("cost_usd"),
                cost_estimated=bool(item.get("cost_estimated")),
                status_code=item.get("status_code"),
                ok=bool(item.get("ok")),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
            )
            for item in exchanges
        ]

    def get_job_request_log(self, job_id: int, index: int) -> JobRequestLogOut | None:
        """Return the full exchange-log entry for one request (1-based index)."""
        row = self.db.get(JobRow, job_id)
        if row is None:
            return None
        log = self.get_job_log(job_id)
        assert log is not None
        exchanges = [entry for entry in (log.entries or []) if entry.get("event") == "exchange"]
        if index < 1 or index > len(exchanges):
            return None
        return JobRequestLogOut(
            job_id=job_id,
            index=index,
            exists=True,
            entry=exchanges[index - 1],
        )

    async def get_job_usage(self, job_id: int) -> JobUsageOut | None:
        """Compose job AI cost from ai_usage_records (authoritative snapshots).

        Exchange-log rows remain available for debug detail, but costs come from
        usage records — never from a live OpenRouter catalog reprice. Old jobs
        without usage rows fall back to the exchange log without live pricing.
        """
        row = self.db.get(JobRow, job_id)
        if row is None:
            return None

        log = self.get_job_log(job_id)
        assert log is not None
        entries = log.entries or []

        usage_rows = list(
            self.db.scalars(
                select(AiUsageRecordRow)
                .where(AiUsageRecordRow.job_id == job_id)
                .order_by(AiUsageRecordRow.created_at.asc(), AiUsageRecordRow.id.asc())
            ).all()
        )

        related_actions: list[JobUsageRelatedOut] = []
        actions = self.list_job_actions(job_id) or []
        for action in actions:
            related = self.db.get(JobRow, action.id)
            if related is None:
                continue
            related_cost = None
            if related.id != job_id:
                related_usage = list(
                    self.db.scalars(
                        select(AiUsageRecordRow).where(AiUsageRecordRow.job_id == related.id)
                    ).all()
                )
                if related_usage:
                    related_cost = micro_to_usd(
                        sum(effective_cost_micro(r) for r in related_usage)
                    )
            related_actions.append(
                JobUsageRelatedOut(
                    id=related.id,
                    action=action.action,
                    status=related.status,
                    model=related.model,
                    datetime=action.datetime,
                    input_tokens=related.input_tokens,
                    output_tokens=related.output_tokens,
                    total_tokens=related.total_tokens,
                    cost_usd=related_cost,
                    current=action.current,
                )
            )

        if usage_rows:
            by_model_map: dict[str, dict] = {}
            by_action_map: dict[str, dict] = {}
            sources: set[str] = set()
            exchange_outs: list[JobUsageExchangeOut] = []
            for index, ur in enumerate(usage_rows, start=1):
                cost = effective_cost_micro(ur)
                cost_usd = micro_to_usd(cost)
                sources.add(ur.pricing_source or "none")
                action_label = job_stats_action_label(ur.operation_type)

                for bucket, key in (
                    (by_model_map, ur.model_id),
                    (by_action_map, action_label),
                ):
                    item = bucket.setdefault(
                        key,
                        {
                            "key": key,
                            "requests": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "cost_micro": 0,
                            "in_price": ur.input_price_micro_usd_per_million,
                            "out_price": ur.output_price_micro_usd_per_million,
                        },
                    )
                    item["requests"] += 1
                    item["input_tokens"] += ur.input_tokens or 0
                    item["output_tokens"] += ur.output_tokens or 0
                    item["total_tokens"] += ur.total_tokens or 0
                    item["cost_micro"] += cost

                exchange_outs.append(
                    JobUsageExchangeOut(
                        index=index,
                        ts=ur.created_at.isoformat() if ur.created_at else None,
                        model=ur.model_id,
                        action=action_label,
                        attempt=None,
                        input_tokens=ur.input_tokens or 0,
                        output_tokens=ur.output_tokens or 0,
                        total_tokens=ur.total_tokens or 0,
                        cost_usd=cost_usd,
                        cost_estimated=ur.actual_cost_micro_usd is None
                        and ur.estimated_cost_micro_usd is not None,
                        status_code=200 if ur.status == "success" else None,
                        ok=ur.status == "success",
                        error=ur.failure_category,
                    )
                )

            by_model = [
                JobUsageModelOut(
                    model=item["key"],
                    name=None,
                    requests=item["requests"],
                    input_tokens=item["input_tokens"],
                    output_tokens=item["output_tokens"],
                    total_tokens=item["total_tokens"],
                    cost_usd=micro_to_usd(item["cost_micro"]),
                    prompt_price_per_million=micro_price_to_per_million(item["in_price"]),
                    completion_price_per_million=micro_price_to_per_million(item["out_price"]),
                )
                for item in sorted(
                    by_model_map.values(),
                    key=lambda x: (-(x["cost_micro"] or 0), -x["total_tokens"], x["key"]),
                )
            ]
            by_action = [
                JobUsageActionOut(
                    action=item["key"],
                    requests=item["requests"],
                    input_tokens=item["input_tokens"],
                    output_tokens=item["output_tokens"],
                    total_tokens=item["total_tokens"],
                    cost_usd=micro_to_usd(item["cost_micro"]),
                )
                for item in sorted(
                    by_action_map.values(),
                    key=lambda x: (-(x["cost_micro"] or 0), -x["total_tokens"], x["key"]),
                )
            ]

            total_cost = sum(effective_cost_micro(r) for r in usage_rows)
            total_tokens = sum(r.total_tokens or 0 for r in usage_rows)
            blended = None
            if total_cost and total_tokens > 0:
                blended = ((micro_to_usd(total_cost) or 0.0) / total_tokens) * 1_000_000

            if sources <= {"openrouter"}:
                pricing_source = "openrouter"
            elif sources <= {"estimated"}:
                pricing_source = "estimated"
            elif "openrouter" in sources and "estimated" in sources:
                pricing_source = "mixed"
            elif sources & {"openrouter", "estimated"}:
                pricing_source = next(iter(sources & {"openrouter", "estimated"}))
            else:
                pricing_source = "none"

            totals = JobUsageTotalsOut(
                requests=len(usage_rows),
                input_tokens=sum(r.input_tokens or 0 for r in usage_rows),
                output_tokens=sum(r.output_tokens or 0 for r in usage_rows),
                total_tokens=total_tokens,
                cost_usd=micro_to_usd(total_cost),
                blended_cost_per_million=blended,
            )
            return JobUsageOut(
                job_id=row.id,
                media_title=row.media_title,
                job_kind=getattr(row, "job_kind", None) or "translate",
                model=row.model,
                status=row.status,
                log_exists=log.exists,
                pricing_source=pricing_source,
                totals=totals,
                by_model=by_model,
                by_action=by_action,
                exchanges=exchange_outs,
                related_actions=related_actions,
            )

        # Legacy fallback: exchange log without live catalog reprice.
        exchanges = parse_exchanges(
            entries,
            fallback_model=row.model,
            pricing_by_model={},
        )
        agg = aggregate_usage(exchanges)
        by_model = [
            JobUsageModelOut(
                model=item["model"],
                name=None,
                requests=item["requests"],
                input_tokens=item["input_tokens"],
                output_tokens=item["output_tokens"],
                total_tokens=item["total_tokens"],
                cost_usd=item["cost_usd"],
                prompt_price_per_million=None,
                completion_price_per_million=None,
            )
            for item in agg["by_model"]
        ]
        by_action = [
            JobUsageActionOut(
                action=item["action"],
                requests=item["requests"],
                input_tokens=item["input_tokens"],
                output_tokens=item["output_tokens"],
                total_tokens=item["total_tokens"],
                cost_usd=item["cost_usd"],
            )
            for item in agg["by_action"]
        ]
        exchange_outs = [
            JobUsageExchangeOut(
                index=item["index"],
                ts=item.get("ts") if isinstance(item.get("ts"), str) else None,
                model=item["model"],
                action=item["action"],
                attempt=item.get("attempt"),
                input_tokens=item["input_tokens"],
                output_tokens=item["output_tokens"],
                total_tokens=item["total_tokens"],
                cost_usd=item.get("cost_usd"),
                cost_estimated=bool(item.get("cost_estimated")),
                status_code=item.get("status_code"),
                ok=bool(item.get("ok")),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
            )
            for item in exchanges
        ]

        if agg["requests"] > 0:
            totals = JobUsageTotalsOut(
                requests=agg["requests"],
                input_tokens=agg["input_tokens"],
                output_tokens=agg["output_tokens"],
                total_tokens=agg["total_tokens"],
                cost_usd=agg["cost_usd"],
                blended_cost_per_million=agg["blended_cost_per_million"],
            )
            pricing_source = agg["pricing_source"]
        else:
            totals = JobUsageTotalsOut(
                requests=0,
                input_tokens=row.input_tokens or 0,
                output_tokens=row.output_tokens or 0,
                total_tokens=row.total_tokens or 0,
                cost_usd=None,
                blended_cost_per_million=None,
            )
            pricing_source = "none"

        return JobUsageOut(
            job_id=row.id,
            media_title=row.media_title,
            job_kind=getattr(row, "job_kind", None) or "translate",
            model=row.model,
            status=row.status,
            log_exists=log.exists,
            pricing_source=pricing_source,
            totals=totals,
            by_model=by_model,
            by_action=by_action,
            exchanges=exchange_outs,
            related_actions=related_actions,
        )

    def stats(self) -> StatsOut:
        counts = dict(
            self.db.execute(
                select(JobRow.status, func.count()).group_by(JobRow.status)
            ).all()
        )
        return StatsOut(
            pending=counts.get("pending", 0),
            processing=counts.get("processing", 0),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            cancelled=counts.get("cancelled", 0),
            skipped=counts.get("skipped", 0),
            total=sum(counts.values()),
        )

    def clear_jobs(
        self,
        *,
        job_kind: str | None = None,
        status: str | None = None,
    ) -> ClearDataResult:
        """Delete job history rows (optionally filtered by kind/status) and their exchange logs."""
        query = select(JobRow.id)
        if job_kind:
            query = query.where(JobRow.job_kind == job_kind)
        if status:
            query = query.where(JobRow.status == status)
        job_ids = list(self.db.scalars(query).all())
        if not job_ids:
            label = status or job_kind or "all"
            return ClearDataResult(
                deleted=0,
                message=f"No {label} jobs to clear.",
                details={
                    "job_kind": job_kind,
                    "status": status,
                    "logs_deleted": 0,
                    "cache_deleted": 0,
                },
            )

        config_dir = get_app_config().config_dir
        logs_deleted = 0
        for job_id in job_ids:
            path = job_openrouter_log_path(config_dir, job_id)
            if path.is_file():
                try:
                    path.unlink()
                    logs_deleted += 1
                except OSError as exc:
                    logger.warning("Could not delete job log %s: %s", path, exc)

        cache_result = self.db.execute(
            delete(TranslationCacheRow).where(TranslationCacheRow.job_id.in_(job_ids))
        )
        cache_deleted = cache_result.rowcount or 0

        self.db.execute(delete(JobRow).where(JobRow.id.in_(job_ids)))
        self.db.commit()
        deleted = len(job_ids)

        if status and job_kind:
            label = f"{status} {job_kind} "
        elif status:
            label = f"{status} "
        elif job_kind:
            label = f"{job_kind} "
        else:
            label = ""
        return ClearDataResult(
            deleted=deleted,
            message=f"Cleared {deleted} {label}job(s).",
            details={
                "job_kind": job_kind,
                "status": status,
                "logs_deleted": logs_deleted,
                "cache_deleted": cache_deleted,
            },
        )

    def clear_usage_stats(self) -> ClearDataResult:
        """Remove OpenRouter exchange logs and reset persisted token totals on jobs."""
        config_dir = get_app_config().config_dir
        logs_dir = config_dir / "logs" / "jobs"
        logs_deleted = 0
        if logs_dir.is_dir():
            for path in logs_dir.glob("job-*-openrouter.jsonl"):
                try:
                    path.unlink()
                    logs_deleted += 1
                except OSError as exc:
                    logger.warning("Could not delete usage log %s: %s", path, exc)

        result = self.db.execute(
            update(JobRow).values(
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        )
        jobs_reset = result.rowcount or 0
        usage_deleted = self.db.execute(delete(AiUsageRecordRow)).rowcount or 0
        routing_deleted = self.db.execute(delete(AiRoutingEventRow)).rowcount or 0
        self.db.commit()

        return ClearDataResult(
            deleted=logs_deleted + usage_deleted,
            message=(
                f"Cleared {logs_deleted} usage log(s), {usage_deleted} AI usage record(s), "
                f"{routing_deleted} routing event(s), and reset token totals on {jobs_reset} job(s)."
            ),
            details={
                "logs_deleted": logs_deleted,
                "jobs_reset": jobs_reset,
                "usage_records_deleted": usage_deleted,
                "routing_events_deleted": routing_deleted,
            },
        )

    async def create_job(
        self,
        payload: JobCreate,
        *,
        candidate: CandidateOut | None = None,
        trigger_type: str = "manual",
        task_id: int | None = None,
    ) -> JobOut:
        # Provider/account validation happens when execution starts, not at create.
        public = self.settings.get_public()

        trigger = trigger_type if trigger_type in {"manual", "automatic"} else "manual"
        candidate_key = payload.candidate_key
        source_path: str | None = payload.source_subtitle_path
        media_path = payload.media_path
        media_type = payload.media_type or "movie"
        media_title = payload.media_title
        source_language = payload.source_language or (public.source_languages[0] if public.source_languages else "en")
        target_language = payload.target_language or public.target_language.code
        bazarr_movie_id = payload.bazarr_movie_id
        bazarr_episode_id = payload.bazarr_episode_id
        bazarr_series_id = payload.bazarr_series_id

        if candidate_key:
            match = candidate
            if match is None or match.key != candidate_key:
                # Planner-resolved local sources should not require a live Bazarr lookup.
                if source_path and Path(source_path).exists():
                    match = None
                else:
                    try:
                        match = await CandidateService(self.db).get_candidate(candidate_key)
                    except Exception:  # noqa: BLE001
                        match = None
            if match is None:
                # Path-based create is allowed when the candidate list lags or the
                # planner already resolved a local source subtitle.
                if not (source_path and Path(source_path).exists()):
                    raise ValueError("Candidate not found. Refresh the list and try again.")
            elif not match.can_translate or not match.source_subtitle_path:
                # Allow path-based automatic chain when candidate list lags.
                if not (source_path and Path(source_path).exists()):
                    raise ValueError(match.reason or "Candidate cannot be translated.")
                if not source_path:
                    source_path = match.source_subtitle_path
                media_path = match.media_path
                media_type = match.media_type
                media_title = match.title
                source_language = match.source_language or source_language
                target_language = match.target_language
                bazarr_movie_id = match.bazarr_movie_id
                bazarr_episode_id = match.bazarr_episode_id
                bazarr_series_id = match.bazarr_series_id
            else:
                source_path = match.source_subtitle_path
                media_path = match.media_path
                media_type = match.media_type
                media_title = match.title
                source_language = match.source_language or source_language
                target_language = match.target_language
                bazarr_movie_id = match.bazarr_movie_id
                bazarr_episode_id = match.bazarr_episode_id
                bazarr_series_id = match.bazarr_series_id

        if not source_path:
            raise ValueError("source_subtitle_path or candidate_key is required")
        if not media_path:
            media_path = str(Path(source_path).parent)

        source = Path(source_path)
        if not is_under_roots(source, public.media_roots):
            raise ValueError("Source subtitle path is outside configured media roots.")
        if not source.exists():
            raise ValueError("Source subtitle file does not exist.")

        target = build_target_subtitle_path(source, target_language, media_path=media_path)
        if target.exists():
            row = JobRow(
                candidate_key=candidate_key,
                job_kind="translate",
                trigger_type=trigger,
                media_type=media_type,
                media_path=media_path,
                media_title=media_title,
                bazarr_movie_id=bazarr_movie_id,
                bazarr_episode_id=bazarr_episode_id,
                bazarr_series_id=bazarr_series_id,
                source_subtitle_path=str(source),
                target_subtitle_path=str(target),
                source_language=source_language,
                target_language=target_language,
                provider_id=None,
                model="",
                status="skipped",
                progress=100,
                reason_code="target_exists",
                error="Target subtitle already exists.",
                completed_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            row = _bind_task_id(self.db, row, task_id)
            return job_to_out(row)

        # Prevent duplicate active jobs for same candidate/target
        existing = None
        if candidate_key:
            existing = self.db.scalar(
                select(JobRow).where(
                    JobRow.job_kind == "translate",
                    JobRow.candidate_key == candidate_key,
                    JobRow.status.in_(["pending", "processing"]),
                )
            )
        if existing is None:
            existing = self.db.scalar(
                select(JobRow).where(
                    JobRow.job_kind == "translate",
                    JobRow.target_subtitle_path == str(target),
                    JobRow.status.in_(["pending", "processing"]),
                )
            )
        if existing:
            existing = _bind_task_id(self.db, existing, task_id)
            return job_to_out(existing)

        src_hash = content_hash(source)
        completed = self.db.scalar(
            select(JobRow)
            .where(
                JobRow.job_kind == "translate",
                JobRow.source_hash == src_hash,
                JobRow.target_language == target_language,
                JobRow.status == "completed",
            )
            .order_by(JobRow.completed_at.desc(), JobRow.id.desc())
        )
        if completed is None:
            # Legacy cache identity (pre-provider / pre-source_hash column match).
            pending_key = pending_dedupe_key(src_hash, target_language)
            completed = self.db.scalar(
                select(JobRow).where(
                    JobRow.job_kind == "translate",
                    JobRow.dedupe_key == pending_key,
                    JobRow.status == "completed",
                )
            )
        if completed and Path(completed.target_subtitle_path).exists():
            row = JobRow(
                candidate_key=candidate_key,
                job_kind="translate",
                trigger_type=trigger,
                media_type=media_type,
                media_path=media_path,
                media_title=media_title,
                bazarr_movie_id=bazarr_movie_id,
                bazarr_episode_id=bazarr_episode_id,
                bazarr_series_id=bazarr_series_id,
                source_subtitle_path=str(source),
                target_subtitle_path=str(target),
                source_language=source_language,
                target_language=target_language,
                provider_id=getattr(completed, "provider_id", None),
                model=completed.model or "",
                status="skipped",
                progress=100,
                reason_code="cache_hit",
                dedupe_key=completed.dedupe_key or pending_dedupe_key(src_hash, target_language),
                source_hash=src_hash,
                error="Identical translation already completed.",
                completed_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            row = _bind_task_id(self.db, row, task_id)
            return job_to_out(row)

        row = JobRow(
            candidate_key=candidate_key,
            job_kind="translate",
            trigger_type=trigger,
            media_type=media_type,
            media_path=media_path,
            media_title=media_title,
            bazarr_movie_id=bazarr_movie_id,
            bazarr_episode_id=bazarr_episode_id,
            bazarr_series_id=bazarr_series_id,
            source_subtitle_path=str(source),
            target_subtitle_path=str(target),
            source_language=source_language,
            target_language=target_language,
            provider_id=None,
            model="",
            status="pending",
            progress=0,
            dedupe_key=pending_dedupe_key(src_hash, target_language),
            source_hash=src_hash,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        row = _bind_task_id(self.db, row, task_id)
        return job_to_out(row)

    async def create_extract_job(
        self,
        payload: ExtractCreate,
        *,
        candidate: CandidateOut | None = None,
        trigger_type: str = "manual",
        task_id: int | None = None,
    ) -> JobOut:
        public = self.settings.get_public()
        trigger = trigger_type if trigger_type in {"manual", "automatic"} else "manual"
        match = candidate
        if match is None or match.key != payload.candidate_key:
            match = await CandidateService(self.db).get_candidate(payload.candidate_key)
        if not match:
            raise ValueError("Candidate not found. Refresh the list and try again.")
        if not match.can_extract or match.extract_stream_index is None:
            raise ValueError(match.reason or "No extractable subtitle track found.")
        if not is_under_roots(match.media_path, public.media_roots):
            raise ValueError("Media path is outside configured media roots.")
        media = Path(match.media_path)
        if not media.exists():
            raise ValueError("Media file is not readable on disk.")

        language = match.extract_language or (
            public.source_languages[0] if public.source_languages else "en"
        )
        output = build_external_subtitle_path(media, language)
        if output.exists():
            row = JobRow(
                candidate_key=match.key,
                job_kind="extract",
                trigger_type=trigger,
                media_type=match.media_type,
                media_path=match.media_path,
                media_title=match.title,
                bazarr_movie_id=match.bazarr_movie_id,
                bazarr_episode_id=match.bazarr_episode_id,
                bazarr_series_id=match.bazarr_series_id,
                source_subtitle_path=match.media_path,
                target_subtitle_path=str(output),
                source_language=language,
                target_language=language,
                model="ffmpeg-extract",
                status="skipped",
                progress=100,
                reason_code="source_exists",
                extract_stream_index=match.extract_stream_index,
                error="External source subtitle already exists.",
                completed_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            row = _bind_task_id(self.db, row, task_id)
            return job_to_out(row)

        existing = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "extract",
                JobRow.candidate_key == match.key,
                JobRow.status.in_(["pending", "processing"]),
            )
        )
        if existing is None:
            existing = self.db.scalar(
                select(JobRow).where(
                    JobRow.job_kind == "extract",
                    JobRow.target_subtitle_path == str(output),
                    JobRow.status.in_(["pending", "processing"]),
                )
            )
        if existing:
            existing = _bind_task_id(self.db, existing, task_id)
            return job_to_out(existing)

        dkey = hashlib.sha256(
            f"extract|{match.media_path}|{language}|{match.extract_stream_index}".encode()
        ).hexdigest()
        extract_track = next(
            (
                item
                for item in match.embedded_subtitles
                if item.stream_index == match.extract_stream_index
            ),
            None,
        )
        method = "tesseract-ocr" if extract_track and extract_track.kind == "image" else "ffmpeg-extract"
        detail = (
            "Queued for PGS OCR extraction"
            if method == "tesseract-ocr"
            else "Queued for extraction"
        )
        row = JobRow(
            candidate_key=match.key,
            job_kind="extract",
            trigger_type=trigger,
            media_type=match.media_type,
            media_path=match.media_path,
            media_title=match.title,
            bazarr_movie_id=match.bazarr_movie_id,
            bazarr_episode_id=match.bazarr_episode_id,
            bazarr_series_id=match.bazarr_series_id,
            source_subtitle_path=match.media_path,
            target_subtitle_path=str(output),
            source_language=language,
            target_language=language,
            model=method,
            status="pending",
            progress=0,
            progress_detail=detail,
            extract_stream_index=match.extract_stream_index,
            dedupe_key=dkey,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        row = _bind_task_id(self.db, row, task_id)
        return job_to_out(row)

    async def create_request_subtitle_job(
        self,
        candidate_key: str,
        language: str | None = None,
        *,
        candidate: CandidateOut | None = None,
        trigger_type: str = "manual",
        task_id: int | None = None,
    ) -> JobOut:
        public = self.settings.get_public()
        trigger = trigger_type if trigger_type in {"manual", "automatic"} else "manual"
        match = candidate
        if match is None or match.key != candidate_key:
            match = await CandidateService(self.db).get_candidate(candidate_key)
        if not match:
            raise ValueError("Candidate not found. Refresh the list and try again.")

        requested = language or (public.source_languages[0] if public.source_languages else "en")
        code2 = to_bazarr_code2(requested)
        expected = build_external_subtitle_path(match.media_path, code2)

        if match.media_type == "movie" and match.bazarr_movie_id is None:
            raise ValueError("Candidate is missing Bazarr movie ID.")
        if match.media_type == "episode" and (
            match.bazarr_episode_id is None or match.bazarr_series_id is None
        ):
            raise ValueError("Candidate is missing Bazarr series/episode IDs.")

        existing = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "request",
                JobRow.candidate_key == match.key,
                JobRow.status.in_(["pending", "processing"]),
            )
        )
        if existing:
            existing = _bind_task_id(self.db, existing, task_id)
            return job_to_out(existing)

        dkey = hashlib.sha256(
            f"request|{match.media_type}|{match.media_path}|{code2}".encode()
        ).hexdigest()
        row = JobRow(
            candidate_key=match.key,
            job_kind="request",
            trigger_type=trigger,
            media_type=match.media_type,
            media_path=match.media_path,
            media_title=match.title,
            bazarr_movie_id=match.bazarr_movie_id,
            bazarr_episode_id=match.bazarr_episode_id,
            bazarr_series_id=match.bazarr_series_id,
            source_subtitle_path=match.media_path,
            target_subtitle_path=str(expected),
            source_language=code2,
            target_language=code2,
            model="bazarr-search",
            status="pending",
            progress=0,
            progress_detail=f"Queued Bazarr search for {code2.upper()}",
            dedupe_key=dkey,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        row = _bind_task_id(self.db, row, task_id)
        return job_to_out(row)

    async def create_request_subtitle_job_for_media(
        self,
        *,
        media_type: str,
        media_path: str,
        media_title: str | None,
        bazarr_movie_id: int | None,
        bazarr_episode_id: int | None,
        bazarr_series_id: int | None,
        target_language: str,
        language: str | None = None,
        trigger_type: str = "manual",
        task_id: int | None = None,
    ) -> JobOut:
        """Create a Bazarr source-request job without a wanted-list candidate."""
        public = self.settings.get_public()
        trigger = trigger_type if trigger_type in {"manual", "automatic"} else "manual"
        requested = language or (public.source_languages[0] if public.source_languages else "en")
        code2 = to_bazarr_code2(requested)
        if media_type == "movie" and bazarr_movie_id is None:
            raise ValueError("Media is missing Bazarr movie ID.")
        if media_type == "episode" and (bazarr_episode_id is None or bazarr_series_id is None):
            raise ValueError("Media is missing Bazarr series/episode IDs.")
        path = media_path or ""
        expected = build_external_subtitle_path(path, code2) if path else Path(f"./{code2}.srt")
        ckey = candidate_key(media_type, path or f"bazarr:{bazarr_movie_id or bazarr_episode_id}", target_language)
        existing = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "request",
                JobRow.candidate_key == ckey,
                JobRow.status.in_(["pending", "processing"]),
            )
        )
        if existing:
            existing = _bind_task_id(self.db, existing, task_id)
            return job_to_out(existing)
        dkey = hashlib.sha256(
            f"request|{media_type}|{path}|{code2}".encode()
        ).hexdigest()
        row = JobRow(
            candidate_key=ckey,
            task_id=task_id,
            job_kind="request",
            trigger_type=trigger,
            media_type=media_type,
            media_path=path or str(expected),
            media_title=media_title,
            bazarr_movie_id=bazarr_movie_id,
            bazarr_episode_id=bazarr_episode_id,
            bazarr_series_id=bazarr_series_id,
            source_subtitle_path=path or str(expected),
            target_subtitle_path=str(expected),
            source_language=code2,
            target_language=code2,
            model="bazarr-search",
            status="pending",
            progress=0,
            progress_detail=f"Queued Bazarr search for {code2.upper()}",
            dedupe_key=dkey,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    @staticmethod
    def _can_request_source(candidate: CandidateOut) -> bool:
        if candidate.reason_code == "target_exists":
            return False
        if candidate.source_subtitle_path:
            return False
        if candidate.media_type == "movie":
            return candidate.bazarr_movie_id is not None
        return candidate.bazarr_episode_id is not None and candidate.bazarr_series_id is not None

    def _recent_not_found_cooldown(self, candidate_key: str) -> JobRow | None:
        """Return a recent not_found request job if still inside the cooldown window."""
        cutoff = utcnow() - timedelta(hours=REQUEST_NOT_FOUND_COOLDOWN_HOURS)
        return self.db.scalar(
            select(JobRow)
            .where(
                JobRow.job_kind == "request",
                JobRow.candidate_key == candidate_key,
                JobRow.reason_code == "not_found",
                JobRow.status.in_(["skipped", "failed"]),
                JobRow.completed_at.is_not(None),
                JobRow.completed_at >= cutoff,
            )
            .order_by(JobRow.completed_at.desc())
            .limit(1)
        )

    async def batch_request_missing_source(
        self,
        language: str | None = None,
    ) -> BatchJobsOut:
        candidates = await CandidateService(self.db).list_candidates()
        jobs: list[JobOut] = []
        created_count = 0
        reused_count = 0
        skipped_count = 0
        errors: list[str] = []

        for match in candidates:
            if match.active_request_job_id is not None:
                skipped_count += 1
                continue
            if not self._can_request_source(match):
                skipped_count += 1
                continue
            if self._recent_not_found_cooldown(match.key):
                skipped_count += 1
                continue
            try:
                existing = self.db.scalar(
                    select(JobRow).where(
                        JobRow.job_kind == "request",
                        JobRow.candidate_key == match.key,
                        JobRow.status.in_(["pending", "processing"]),
                    )
                )
                job = await self.create_request_subtitle_job(
                    match.key,
                    language=language,
                    candidate=match,
                )
                jobs.append(job)
                if existing and existing.id == job.id:
                    reused_count += 1
                else:
                    created_count += 1
            except (ValueError, BazarrError) as exc:
                errors.append(f"{match.title}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{match.title}: {exc}")

        return BatchJobsOut(
            jobs=jobs,
            created_count=created_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            errors=errors,
        )

    async def batch_extract(self) -> BatchJobsOut:
        candidates = await CandidateService(self.db).list_candidates()
        jobs: list[JobOut] = []
        created_count = 0
        reused_count = 0
        skipped_count = 0
        errors: list[str] = []

        for match in candidates:
            if not match.can_extract:
                skipped_count += 1
                continue
            if match.active_extract_job_id is not None:
                skipped_count += 1
                continue
            try:
                existing = self.db.scalar(
                    select(JobRow).where(
                        JobRow.job_kind == "extract",
                        JobRow.candidate_key == match.key,
                        JobRow.status.in_(["pending", "processing"]),
                    )
                )
                job = await self.create_extract_job(
                    ExtractCreate(candidate_key=match.key),
                    candidate=match,
                )
                jobs.append(job)
                if existing and existing.id == job.id:
                    reused_count += 1
                else:
                    created_count += 1
            except (ValueError, EmbeddedError) as exc:
                errors.append(f"{match.title}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{match.title}: {exc}")

        return BatchJobsOut(
            jobs=jobs,
            created_count=created_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            errors=errors,
        )

    async def batch_translate(self) -> BatchJobsOut:
        candidates = await CandidateService(self.db).list_candidates()
        jobs: list[JobOut] = []
        created_count = 0
        reused_count = 0
        skipped_count = 0
        errors: list[str] = []

        for match in candidates:
            if not match.can_translate:
                skipped_count += 1
                continue
            if match.active_translate_job_id is not None:
                skipped_count += 1
                continue
            try:
                existing = self.db.scalar(
                    select(JobRow).where(
                        JobRow.job_kind == "translate",
                        JobRow.candidate_key == match.key,
                        JobRow.status.in_(["pending", "processing"]),
                    )
                )
                job = await self.create_job(
                    JobCreate(candidate_key=match.key),
                    candidate=match,
                )
                jobs.append(job)
                if existing and existing.id == job.id:
                    reused_count += 1
                else:
                    created_count += 1
            except (ValueError, AIProviderError) as exc:
                errors.append(f"{match.title}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{match.title}: {exc}")

        return BatchJobsOut(
            jobs=jobs,
            created_count=created_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            errors=errors,
        )

    def claim_next_job(self, job_kind: str | None = None) -> JobRow | None:
        query = (
            select(JobRow)
            .where(JobRow.status == "pending")
            .order_by(
                # Manual jobs before automatic ("manual" > "automatic"); then oldest first.
                JobRow.trigger_type.desc(),
                JobRow.created_at.asc(),
            )
            .limit(1)
        )
        if job_kind:
            query = query.where(JobRow.job_kind == job_kind)
        row = self.db.scalar(query)
        if not row:
            return None
        # Optimistic claim so parallel workers cannot steal the same row.
        result = self.db.execute(
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
            self.db.rollback()
            return None
        self.db.commit()
        self.db.refresh(row)
        task_id = getattr(row, "task_id", None)
        if task_id is not None:
            task = self.db.get(LocalizationTaskRow, task_id)
            if task is not None and task.status == "cancelled":
                row.status = "cancelled"
                row.completed_at = utcnow()
                row.progress_detail = "Cancelled with localization task"
                row.reason_code = "cancelled"
                self.db.add(row)
                self.db.commit()
                return None
        return row

    @staticmethod
    def recover_interrupted_jobs(db: Session) -> int:
        """Reset orphaned processing jobs to pending after restart."""
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

    def cancel_job(self, job_id: int) -> JobOut:
        row = self.db.get(JobRow, job_id)
        if not row:
            raise ValueError("Job not found")
        if row.status not in {"pending", "processing"}:
            raise ValueError("Only pending or processing jobs can be cancelled")
        row.status = "cancelled"
        row.completed_at = utcnow()
        row.progress_detail = "Cancelled by user"
        row.reason_code = "cancelled"
        self.db.add(row)
        self.db.commit()
        return job_to_out(row)

    async def retry_job(self, job_id: int) -> JobOut:
        row = self.db.get(JobRow, job_id)
        if not row:
            raise ValueError("Job not found")
        kind = getattr(row, "job_kind", None) or "translate"
        if kind == "extract":
            if not row.candidate_key:
                raise ValueError("Extract job is missing candidate key")
            return await self.create_extract_job(
                ExtractCreate(candidate_key=row.candidate_key),
                task_id=getattr(row, "task_id", None),
            )
        if kind == "request":
            if not row.candidate_key:
                raise ValueError("Request job is missing candidate key")
            return await self.create_request_subtitle_job(
                row.candidate_key,
                language=row.source_language,
                task_id=getattr(row, "task_id", None),
            )
        payload = JobCreate(
            candidate_key=row.candidate_key,
            source_subtitle_path=row.source_subtitle_path,
            target_language=row.target_language,
            media_type=row.media_type,  # type: ignore[arg-type]
            media_path=row.media_path,
            media_title=row.media_title,
            bazarr_movie_id=row.bazarr_movie_id,
            bazarr_episode_id=row.bazarr_episode_id,
            bazarr_series_id=row.bazarr_series_id,
            source_language=row.source_language,
        )
        return await self.create_job(payload, task_id=getattr(row, "task_id", None))

    async def retry_bazarr_sync(self, job_id: int) -> JobOut:
        """Job-level Bazarr retry for the jobs UI and legacy non-task-backed jobs.

        Task-backed verification retries belong to TaskPlanner + BazarrVerificationService.
        """
        row = self.db.get(JobRow, job_id)
        if not row:
            raise ValueError("Job not found")
        if row.status != "completed":
            raise ValueError("Bazarr sync retry is only available for completed jobs")
        from app.localization.verification import BazarrVerificationService

        result = await BazarrVerificationService(self.db).rescan_and_verify_job(row)
        if result.ok:
            row.warning = None
            row.reason_code = None
        else:
            row.warning = result.message
            row.reason_code = result.reason_code
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    async def process_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return
        kind = getattr(row, "job_kind", None) or "translate"
        try:
            if kind == "extract":
                await self._process_extract_job(job_id)
            elif kind == "request":
                await self._process_request_subtitle_job(job_id)
            else:
                await self._process_translate_job(job_id)
        finally:
            await self._notify_task_planner(job_id)

    async def _process_request_subtitle_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return
        log = get_logger("jobs")
        label = (row.source_language or "en").upper()
        try:
            public = self.settings.get_public()
            bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
            if not bazarr_url:
                raise BazarrError("Bazarr URL is not configured")
            client = BazarrClient(bazarr_url, bazarr_key)
            mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
            code2 = to_bazarr_code2(row.source_language or "en")

            # Fast path: subtitle may already be on disk (including .en.hi.srt).
            found_path = await self._lookup_requested_subtitle(client, row, code2, mappings)
            if found_path:
                await self._complete_request_job(job_id, found_path, label)
                await self._maybe_chain_automatic_translate(job_id, found_path)
                return

            row.progress = 5
            row.progress_detail = f"Asking Bazarr to search for {label}"
            self.db.add(row)
            self.db.commit()

            await self._trigger_bazarr_download(client, row, code2, hi=False)

            row = self.db.get(JobRow, job_id)
            if not row or row.status == "cancelled":
                return
            row.progress = 15
            row.progress_detail = f"Waiting for Bazarr to finish searching for {label}"
            self.db.add(row)
            self.db.commit()

            deadline = monotonic() + REQUEST_TIMEOUT_SECONDS
            hi_requested = False
            found_path = None
            while monotonic() < deadline:
                row = self.db.get(JobRow, job_id)
                if not row or row.status == "cancelled":
                    return

                found_path = await self._lookup_requested_subtitle(
                    client,
                    row,
                    code2,
                    mappings,
                )
                if found_path:
                    break

                elapsed = REQUEST_TIMEOUT_SECONDS - (deadline - monotonic())
                if not hi_requested and elapsed >= REQUEST_HI_RETRY_AFTER_SECONDS:
                    hi_requested = True
                    row.progress_detail = f"Also searching Bazarr for {label} (HI)"
                    self.db.add(row)
                    self.db.commit()
                    try:
                        await self._trigger_bazarr_download(client, row, code2, hi=True)
                    except BazarrError as exc:
                        log.warning(
                            "HI Bazarr search failed job_id=%s error=%s",
                            job_id,
                            exc,
                        )

                pct = min(90, 15 + int(75 * (elapsed / REQUEST_TIMEOUT_SECONDS)))
                row = self.db.get(JobRow, job_id)
                if not row or row.status == "cancelled":
                    return
                row.progress = pct
                row.progress_detail = f"Still searching for {label} via Bazarr…"
                self.db.add(row)
                self.db.commit()
                await asyncio.sleep(REQUEST_POLL_SECONDS)

            row = self.db.get(JobRow, job_id)
            if not row or row.status == "cancelled":
                return

            if found_path:
                await self._complete_request_job(job_id, found_path, label)
                await self._maybe_chain_automatic_translate(job_id, found_path)
                return

            # Fallback: extract an embedded text or PGS track when Bazarr finds nothing.
            extracted = await self._extract_fallback_for_request(row, code2)
            if extracted:
                row = self.db.get(JobRow, job_id)
                if not row or row.status == "cancelled":
                    return
                row.source_subtitle_path = extracted
                row.target_subtitle_path = extracted
                row.status = "completed"
                row.progress = 100
                row.progress_detail = f"Extracted embedded {label} subtitle"
                row.completed_at = utcnow()
                row.error = None
                row.warning = (
                    f"Bazarr found no external {label} subtitle within "
                    f"{int(REQUEST_TIMEOUT_SECONDS)}s; used embedded extract fallback."
                )
                row.reason_code = None
                self.db.add(row)
                self.db.commit()
                log.info("Request job completed via extract fallback job_id=%s path=%s", job_id, extracted)
                await self._maybe_chain_automatic_translate(job_id, extracted)
                return

            # Nothing available — skip (not fail) so retries/batch don't thrash.
            row.status = "skipped"
            row.progress = 100
            row.progress_detail = f"No {label} subtitle found"
            row.error = (
                f"Bazarr searched for {label} but no external subtitle appeared "
                f"within {int(REQUEST_TIMEOUT_SECONDS)}s, and no extractable embedded "
                f"track was available."
            )
            row.reason_code = "not_found"
            row.completed_at = utcnow()
            self.db.add(row)
            self.db.commit()
            log.info("Request job skipped without subtitle job_id=%s", job_id)
        except Exception as exc:  # noqa: BLE001
            log.error("Request job failed job_id=%s error=%s", job_id, exc)
            current = self.db.get(JobRow, job_id)
            if current and current.status != "cancelled":
                current.status = "failed"
                current.error = _public_error(exc)
                current.reason_code = _reason_code(exc)
                current.completed_at = utcnow()
                self.db.add(current)
                self.db.commit()

    async def _complete_request_job(self, job_id: int, found_path: str, label: str) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status == "cancelled":
            return
        row.source_subtitle_path = found_path
        row.target_subtitle_path = found_path
        row.status = "completed"
        row.progress = 100
        row.progress_detail = f"Found {label} subtitle"
        row.completed_at = utcnow()
        row.error = None
        row.reason_code = None
        self.db.add(row)
        self.db.commit()
        self._set_task_checkpoints(getattr(row, "task_id", None), source="done")
        get_logger("jobs").info("Request job completed job_id=%s path=%s", job_id, found_path)

    async def _trigger_bazarr_download(
        self,
        client: BazarrClient,
        row: JobRow,
        code2: str,
        *,
        hi: bool,
    ) -> None:
        if row.media_type == "movie":
            if row.bazarr_movie_id is None:
                raise ValueError("Missing Bazarr movie ID")
            await client.download_movie_subtitle(row.bazarr_movie_id, code2, hi=hi)
            return
        if row.bazarr_episode_id is None or row.bazarr_series_id is None:
            raise ValueError("Missing Bazarr series/episode IDs")
        await client.download_episode_subtitle(
            row.bazarr_series_id,
            row.bazarr_episode_id,
            code2,
            hi=hi,
        )

    async def _extract_fallback_for_request(self, row: JobRow, code2: str) -> str | None:
        media = Path(row.media_path)
        if not media.is_file():
            return None
        try:
            tracks = await probe_subtitle_tracks(media)
        except EmbeddedError:
            return None
        track = pick_extractable_track(tracks, [code2, row.source_language or code2])
        if track is None or track.stream_index is None:
            return None
        output = build_external_subtitle_path(media, code2)
        if output.exists() and output.stat().st_size > 0:
            return str(output)
        try:
            await extract_embedded_track(media, track.stream_index, output, language=code2)
        except EmbeddedError as exc:
            get_logger("jobs").warning(
                "Extract fallback failed job_id=%s error=%s",
                row.id,
                exc,
            )
            return None
        return str(output)

    async def _lookup_requested_subtitle(
        self,
        client: BazarrClient,
        row: JobRow,
        language: str,
        mappings: list[PathMapping],
    ) -> str | None:
        detail = None
        if row.media_type == "movie" and row.bazarr_movie_id is not None:
            detail = await client.get_movie(row.bazarr_movie_id)
        elif row.media_type == "episode" and row.bazarr_episode_id is not None:
            detail = await client.get_episode(row.bazarr_episode_id)

        candidates: list[str] = []
        if detail:
            for sub in client.parse_subtitles(detail):
                if not sub.path or not str(sub.path).lower().endswith(".srt"):
                    continue
                code = normalize_language_code(sub.language_code)
                if code and language_matches(code, [language]):
                    candidates.append(apply_path_mapping(sub.path, mappings))

        expected = Path(row.target_subtitle_path)
        if expected.exists() and expected.stat().st_size > 0:
            return str(expected)

        # Accept HI/SDH sidecars beside the media even when Bazarr metadata lags.
        media = Path(row.media_path)
        found_local = find_source_srt_beside_media(media, [language])
        if found_local:
            path, _lang = found_local
            if path.exists() and path.stat().st_size > 0:
                return str(path)

        if media.parent.is_dir():
            stem = media.stem
            for path in sorted(media.parent.glob("*.srt")):
                match = LANG_SUFFIX_RE.match(path.name)
                if not match or match.group("stem") != stem:
                    continue
                code = normalize_language_code(match.group("lang"))
                if code and language_matches(code, [language]) and path.stat().st_size > 0:
                    return str(path)

        for path in candidates:
            local = Path(path)
            if local.exists() and local.stat().st_size > 0:
                return str(local)
            # Bazarr may report the path before our mount sees it; still treat metadata as success
            if path.lower().endswith(".srt"):
                return path
        return None

    async def _process_extract_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return
        log = get_logger("jobs")
        try:
            if row.extract_stream_index is None:
                raise EmbeddedError("Missing embedded stream index for extraction.")
            row.progress = 10
            row.progress_detail = (
                "OCR embedded PGS subtitles (this can take several minutes)"
                if (row.model or "").startswith("tesseract")
                else "Extracting embedded text track"
            )
            self.db.add(row)
            self.db.commit()

            await extract_embedded_track(
                row.media_path,
                row.extract_stream_index,
                row.target_subtitle_path,
                language=row.source_language or "en",
            )

            current = self.db.get(JobRow, job_id)
            if not current or current.status == "cancelled":
                return
            current.progress = 90
            current.progress_detail = "Extracted"
            current.status = "completed"
            current.completed_at = utcnow()

            try:
                await self._rescan(current)
            except Exception as exc:  # noqa: BLE001
                log.warning("Bazarr rescan failed after extract job_id=%s error=%s", job_id, exc)
                current.warning = str(exc)
                current.reason_code = "bazarr_rescan_failed"

            current.progress = 100
            current.progress_detail = "Done"
            self.db.add(current)
            self.db.commit()
            self._set_task_checkpoints(
                getattr(current, "task_id", None), source="done", extract="done"
            )
            log.info("Extract job completed job_id=%s path=%s", job_id, current.target_subtitle_path)
            await self._maybe_chain_automatic_translate(job_id, current.target_subtitle_path)
        except Exception as exc:  # noqa: BLE001
            log.error("Extract job failed job_id=%s error=%s", job_id, exc)
            current = self.db.get(JobRow, job_id)
            if current and current.status != "cancelled":
                current.status = "failed"
                current.error = _public_error(exc)
                current.reason_code = _reason_code(exc)
                current.completed_at = utcnow()
                self.db.add(current)
                self.db.commit()
                self._set_task_checkpoints(getattr(current, "task_id", None), extract="failed")

    async def _notify_task_planner(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if row is None or not getattr(row, "task_id", None):
            return
        from app.localization.planner import TaskPlanner

        try:
            await TaskPlanner(self.db).on_job_finished(job_id)
        except Exception as exc:  # noqa: BLE001
            get_logger("jobs").warning(
                "Task planner notify failed job_id=%s error=%s",
                job_id,
                exc,
            )

    async def _maybe_chain_automatic_translate(self, job_id: int, source_path: str) -> None:
        # Legacy compatibility path.
        # Task-backed executions are orchestrated exclusively by TaskPlanner.
        row = self.db.get(JobRow, job_id)
        if not row or (getattr(row, "trigger_type", None) or "manual") != "automatic":
            return
        # Task-backed jobs continue via TaskPlanner — do not double-chain.
        if getattr(row, "task_id", None):
            return
        if not row.candidate_key:
            return
        if not self.settings.is_automatic_fallback_enabled():
            return
        from app.services.fallback import FallbackPlanner

        try:
            chained = await FallbackPlanner(self.db).maybe_chain_translate(
                candidate_key=row.candidate_key,
                source_path=source_path,
            )
            if chained:
                get_logger("jobs").info(
                    "Chained automatic translate job_id=%s from=%s",
                    chained.id,
                    job_id,
                )
        except Exception as exc:  # noqa: BLE001
            get_logger("jobs").warning(
                "Automatic translate chain failed from_job=%s error=%s",
                job_id,
                exc,
            )

    def _set_task_checkpoints(self, task_id: int | None, **states: str) -> None:
        if not task_id:
            return
        from app.localization.checkpoints import merge_checkpoints
        from app.db.models import LocalizationTaskRow

        task = self.db.get(LocalizationTaskRow, task_id)
        if task is None:
            return
        task.metadata_json = merge_checkpoints(task.metadata_json, states)
        self.db.add(task)
        self.db.commit()

    async def _process_translate_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return

        log = get_logger("jobs")
        exchange_log: JobOpenRouterExchangeLog | None = None
        usage_service = AiUsageService(self.db)
        budget = AiBudgetService(self.db)
        router = ModelRouter(self.db)
        task_id = getattr(row, "task_id", None)
        try:
            public = self.settings.get_public()
            bootstrap_providers(self.db)

            source_path = Path(row.source_subtitle_path)
            content = source_path.read_text(encoding="utf-8")
            document = parse_srt(content)
            source_validation = validate_source(document)
            if not source_validation.ok:
                raise ValueError(source_validation.error_message)

            chars = sum(len(block.text) for block in document.blocks)
            from app.services.ai_cost import estimate_conservative_job_tokens

            _, estimated_input, estimated_output = estimate_conservative_job_tokens(
                char_count=chars
            )
            trigger = getattr(row, "trigger_type", None) or "manual"
            routing = router.select_models(
                job=row,
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=estimated_output,
                trigger_type=trigger,
                char_count=chars,
            )
            if not routing.candidates:
                reason = routing.blocked_reason or "no_compatible_model"
                raise RoutingBlockedError(
                    "No eligible model for this translation. "
                    + (
                        "Blocked by cost or monthly budget."
                        if reason == "blocked_by_cost_policy"
                        else "Configure a compatible model in Settings → Models."
                    ),
                    reason_code=reason,
                )

            resolved = routing.candidates[0]
            resolved_model = resolved.model_id
            resolved_provider = resolved.provider_id
            config = get_app_config()
            exchange_log = JobOpenRouterExchangeLog(
                job_openrouter_log_path(config.config_dir, job_id),
                job_id=job_id,
            )
            exchange_log.record(
                {
                    "event": "job_start",
                    "provider": resolved_provider,
                    "model": resolved_model,
                    "candidates": [
                        f"{c.provider_id}/{c.model_id}" for c in routing.candidates
                    ],
                    "strategy": routing.strategy,
                    "media_title": row.media_title,
                    "media_path": row.media_path,
                    "source_subtitle_path": row.source_subtitle_path,
                    "target_subtitle_path": row.target_subtitle_path,
                    "source_language": row.source_language,
                    "target_language": row.target_language,
                    "batch_size": public.batch_size,
                    "block_count": len(document.blocks),
                    "log_path": str(exchange_log.path),
                }
            )
            log.info("AI exchange log job_id=%s path=%s", job_id, exchange_log.path)

            target_language_name = public.target_language.name
            if task_id:
                from app.db.models import LocalizationTaskRow

                task_row = self.db.get(LocalizationTaskRow, int(task_id))
                if task_row is not None:
                    target_language_name = task_row.target_language_name

            current = self.db.get(JobRow, job_id)
            if current:
                current.progress = 5
                current.progress_detail = "Starting translation"
                current.model = resolved_model
                current.provider_id = resolved_provider
                self.db.add(current)
                self.db.commit()
            self._set_task_checkpoints(task_id, translate="active")

            checkpoint = TranslationCheckpoint()
            outcome = None
            last_error: Exception | None = None
            winning_model = resolved_model
            winning_provider = resolved_provider
            repair_used = False

            registry = get_provider_registry()

            for index, candidate in enumerate(routing.candidates):
                reservation = None
                try:
                    reservation = budget.reserve(
                        amount_micro_usd=int(candidate.estimated_cost_micro_usd or 0),
                        job_id=job_id,
                        trigger_type=trigger,
                        tier=candidate.tier,
                    )
                except BudgetBlockedError as exc:
                    router.record_fallback(
                        job_id=job_id,
                        model_id=candidate.model_id,
                        next_model_id=None,
                        failure_category="budget_blocked",
                        strategy=routing.strategy,
                        provider_id=candidate.provider_id,
                    )
                    last_error = RoutingBlockedError(str(exc), reason_code="blocked_by_cost_policy")
                    continue

                base_provider = registry.get(candidate.provider_id)
                if isinstance(base_provider, OpenRouterProvider):
                    provider = base_provider.with_exchange_log(
                        exchange_log,
                        log_full_exchanges=bool(public.openrouter_log_full_exchanges),
                    )
                else:
                    provider = base_provider

                recording = RecordingAIProvider(
                    provider,
                    usage_service,
                    job_id=job_id,
                    trigger_type=trigger,
                    tier=candidate.tier,
                    attempt_number=index + 1,
                    provider_id=candidate.provider_id,
                )
                service = TranslationService(
                    recording, temperature=float(public.openrouter_temperature)
                )

                current = self.db.get(JobRow, job_id)
                if current:
                    current.model = candidate.model_id
                    current.provider_id = candidate.provider_id
                    current.progress_detail = f"Using {candidate.provider_id}/{candidate.model_id}"
                    self.db.add(current)
                    self.db.commit()

                try:
                    async def on_progress(done: int, total: int) -> None:
                        current = self.db.get(JobRow, job_id)
                        if not current or current.status == "cancelled":
                            raise RuntimeError("Job cancelled")
                        current.progress = round(5 + (done / total) * 95, 2)
                        current.progress_detail = f"{done} / {total} batches"
                        self.db.add(current)
                        self.db.commit()

                    outcome = await service.translate_document(
                        document,
                        model=candidate.model_id,
                        target_language_code=row.target_language,
                        target_language_name=target_language_name,
                        batch_size=public.batch_size,
                        progress_callback=on_progress,
                        checkpoint=checkpoint,
                        provider_id=candidate.provider_id,
                    )
                    winning_model = candidate.model_id
                    winning_provider = candidate.provider_id
                    repair_used = bool(outcome.repair_used)
                    budget.release(reservation)
                    last_error = None
                    break
                except RetryableTranslationError as exc:
                    checkpoint = exc.checkpoint
                    category = classify_provider_failure(exc)
                    next_cand = (
                        routing.candidates[index + 1]
                        if index + 1 < len(routing.candidates)
                        else None
                    )
                    router.record_fallback(
                        job_id=job_id,
                        model_id=candidate.model_id,
                        next_model_id=next_cand.model_id if next_cand else None,
                        failure_category=category,
                        strategy=routing.strategy,
                        provider_id=candidate.provider_id,
                        next_provider_id=next_cand.provider_id if next_cand else None,
                    )
                    last_error = exc
                    budget.release(reservation)
                    self.db.commit()
                    if next_cand is None:
                        raise
                    continue
                except AIProviderError as exc:
                    category = classify_provider_failure(exc)
                    budget.release(reservation)
                    if is_technical_failure(category) and index + 1 < len(routing.candidates):
                        next_cand = routing.candidates[index + 1]
                        router.record_fallback(
                            job_id=job_id,
                            model_id=candidate.model_id,
                            next_model_id=next_cand.model_id,
                            failure_category=category,
                            strategy=routing.strategy,
                            provider_id=candidate.provider_id,
                            next_provider_id=next_cand.provider_id,
                        )
                        last_error = exc
                        self.db.commit()
                        continue
                    raise
                except Exception:
                    budget.release(reservation)
                    raise
            else:
                if last_error is not None:
                    raise last_error
                raise RoutingBlockedError(
                    "No eligible model for this translation.",
                    reason_code="no_compatible_model",
                )

            assert outcome is not None

            current = self.db.get(JobRow, job_id)
            if not current or current.status == "cancelled":
                usage_service.set_translation_outcomes(job_id, "cancelled")
                exchange_log.record({"event": "job_end", "status": "cancelled"})
                self.db.commit()
                return

            self._set_task_checkpoints(
                task_id, translate="done", validate="done", write="active"
            )
            target_path = Path(current.target_subtitle_path)
            write_srt_atomic(target_path, outcome.document, overwrite=False)
            ensure_canonical_sidecar(target_path, current.target_language)
            self._set_task_checkpoints(task_id, write="done", sync="active")

            current.model = winning_model
            current.provider_id = winning_provider
            current.dedupe_key = dedupe_key(
                current.source_hash or "",
                current.target_language,
                winning_model,
                provider_id=winning_provider,
            )
            current.input_tokens = outcome.usage.input_tokens
            current.output_tokens = outcome.usage.output_tokens
            current.total_tokens = outcome.usage.total_tokens
            current.progress = 100
            current.progress_detail = "Written"
            current.status = "completed"
            current.completed_at = utcnow()
            if outcome.warnings:
                current.warning = "; ".join(outcome.warnings)
                current.reason_code = "markup_warning"
            else:
                current.warning = None

            self.db.add(
                TranslationCacheRow(
                    source_hash=current.source_hash or "",
                    target_language=current.target_language,
                    provider_id=winning_provider,
                    model=current.model,
                    target_subtitle_path=current.target_subtitle_path,
                    job_id=current.id,
                )
            )
            usage_service.set_translation_outcomes(
                job_id, "success_with_repair" if repair_used else "perfect_success"
            )

            try:
                await self._rescan(current)
                self._set_task_checkpoints(task_id, sync="done", verify="active")
                verified = await self._verify_target_with_backoff(current)
                if not verified:
                    self._set_task_checkpoints(task_id, verify="failed")
                    verify_warning = (
                        "Bazarr rescan succeeded but the target subtitle was still "
                        "reported missing after verification retries."
                    )
                    if current.warning:
                        current.warning = f"{current.warning}; {verify_warning}"
                    else:
                        current.warning = verify_warning
                    current.reason_code = "bazarr_verify_failed"
                else:
                    self._set_task_checkpoints(task_id, verify="done")
            except Exception as exc:  # noqa: BLE001
                log.warning("Bazarr rescan failed job_id=%s error=%s", job_id, exc)
                self._set_task_checkpoints(task_id, sync="failed", verify="failed")
                log.warning("Bazarr rescan failed job_id=%s error=%s", job_id, exc)
                rescan_warning = str(exc)
                if current.warning:
                    current.warning = f"{current.warning}; {rescan_warning}"
                else:
                    current.warning = rescan_warning
                current.reason_code = "bazarr_rescan_failed"

            self.db.add(current)
            self.db.commit()
            exchange_log.record(
                {
                    "event": "job_end",
                    "status": "completed",
                    "model": winning_model,
                    "input_tokens": outcome.usage.input_tokens,
                    "output_tokens": outcome.usage.output_tokens,
                    "total_tokens": outcome.usage.total_tokens,
                    "warning": current.warning,
                    "translation_warnings": outcome.warnings,
                }
            )
            log.info("Job completed job_id=%s model=%s", job_id, winning_model)
        except Exception as exc:  # noqa: BLE001
            log.error("Job failed job_id=%s error=%s", job_id, exc)
            current = self.db.get(JobRow, job_id)
            reason = _reason_code(exc)
            if current and current.status != "cancelled":
                current.status = "failed"
                current.error = _public_error(exc)
                current.reason_code = reason
                current.completed_at = utcnow()
                self.db.add(current)
            if reason == "validation_failed":
                self._set_task_checkpoints(task_id, translate="done", validate="failed")
                usage_service.set_translation_outcomes(job_id, "validation_failure")
            elif reason in {"blocked_by_cost_policy"}:
                usage_service.set_translation_outcomes(job_id, "budget_blocked")
            elif reason == "cancelled":
                usage_service.set_translation_outcomes(job_id, "cancelled")
            else:
                self._set_task_checkpoints(task_id, translate="failed")
                usage_service.set_translation_outcomes(job_id, "technical_failure")
            self.db.commit()
            if exchange_log is not None:
                exchange_log.record(
                    {
                        "event": "job_end",
                        "status": "failed",
                        "error": _public_error(exc),
                        "reason_code": reason,
                    }
                )

    async def _rescan(self, row: JobRow) -> None:
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            raise BazarrError("Bazarr URL is not configured")
        client = BazarrClient(bazarr_url, bazarr_key)
        if row.media_type == "movie" and row.bazarr_movie_id is not None:
            await client.rescan_movie(row.bazarr_movie_id)
        elif row.media_type == "episode" and row.bazarr_episode_id is not None:
            await client.rescan_episode(row.bazarr_episode_id, row.bazarr_series_id)
        else:
            raise BazarrError("Missing Bazarr media identifiers for rescan")

    async def _verify_target_present(self, row: JobRow) -> bool:
        """Check disk + Bazarr metadata for the target subtitle."""
        target = Path(row.target_subtitle_path)
        if not target.is_file() or target.stat().st_size <= 0:
            return False

        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            return False
        client = BazarrClient(bazarr_url, bazarr_key)
        detail = None
        if row.media_type == "movie" and row.bazarr_movie_id is not None:
            detail = await client.get_movie(row.bazarr_movie_id)
        elif row.media_type == "episode" and row.bazarr_episode_id is not None:
            detail = await client.get_episode(row.bazarr_episode_id)
        return BazarrClient.target_subtitle_present(detail, row.target_language)

    async def _verify_target_with_backoff(self, row: JobRow) -> bool:
        delays = (2.0, 5.0, 10.0)
        for delay in delays:
            await asyncio.sleep(delay)
            try:
                if await self._verify_target_present(row):
                    return True
            except Exception as exc:  # noqa: BLE001
                get_logger("jobs").warning(
                    "Bazarr verify attempt failed job_id=%s error=%s",
                    row.id,
                    exc,
                )
        return False


def _public_error(exc: Exception) -> str:
    if isinstance(exc, RoutingBlockedError):
        return str(exc)
    if isinstance(exc, BudgetBlockedError):
        return str(exc)
    if isinstance(exc, AIProviderError):
        return user_message_for_provider_error(exc)
    if isinstance(exc, BazarrError):
        return str(exc)
    if isinstance(exc, EmbeddedError):
        return str(exc)
    message = str(exc)
    mapping = {
        "Target subtitle already exists": "Target subtitle already exists.",
        "No compatible source": "No compatible source subtitle was found.",
        "validation": "Translation response failed validation.",
        "extractable": "No extractable subtitle track found.",
        "ffmpeg": "Embedded subtitle extraction failed.",
        "tesseract": "PGS subtitle OCR failed.",
        "OCR": "PGS subtitle OCR failed.",
    }
    for key, value in mapping.items():
        if key.lower() in message.lower():
            return value
    return message


def _reason_code(exc: Exception) -> str:
    if isinstance(exc, RoutingBlockedError):
        return exc.reason_code
    if isinstance(exc, BudgetBlockedError):
        return "blocked_by_cost_policy"
    if isinstance(exc, AIProviderError):
        category = getattr(exc, "category", None) or "provider_error"
        if category == "validation_error" or "validation" in str(exc).lower():
            return "validation_failed"
        if category == "auth_error" or getattr(exc, "status_code", None) == 401:
            return "provider_auth"
        if category == "rate_limit":
            return "rate_limit"
        if category == "context_overflow":
            return "context_limit"
        if category == "incompatible":
            return "model_not_found"
        if category == "timeout":
            return "provider_timeout"
        return "provider_error"
    if isinstance(exc, BazarrError):
        return "bazarr_error"
    if isinstance(exc, EmbeddedError):
        return "extract_failed"
    if "cancel" in str(exc).lower():
        return "cancelled"
    return "failed"
