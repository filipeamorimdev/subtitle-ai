"""Job service and worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import delete, func, select, update
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
from app.db.models import JobRow, TranslationCacheRow
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.bazarr.paths import (
    PathMapping,
    apply_path_mapping,
    is_under_roots,
    mappings_from_settings,
)
from app.services.candidates import CandidateService, to_bazarr_code2
from app.services.glossary import GlossaryService
from app.services.settings import SettingsService
from app.subtitles.embedded import (
    EmbeddedError,
    extract_text_track,
    pick_extractable_track,
    probe_subtitle_tracks,
)
from app.subtitles.filenames import (
    LANG_SUFFIX_RE,
    build_external_subtitle_path,
    build_target_subtitle_path,
    find_source_srt_beside_media,
    language_matches,
    normalize_language_code,
)
from app.subtitles.parsers.srt import parse_srt
from app.subtitles.validation import validate_source
from app.subtitles.writer.srt import write_srt_atomic
from app.jobs.usage import ModelPricing, aggregate_usage, estimate_cost_usd, parse_exchanges
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError
from app.translation.openrouter.exchange_log import JobOpenRouterExchangeLog, job_openrouter_log_path
from app.translation.openrouter.service import OpenRouterTranslationService

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


def dedupe_key(source_hash: str, target_language: str, model: str) -> str:
    raw = f"{source_hash}|{target_language}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def job_to_out(row: JobRow) -> JobOut:
    return JobOut(
        id=row.id,
        candidate_key=row.candidate_key,
        job_kind=getattr(row, "job_kind", None) or "translate",
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

        actions: list[JobActionOut] = []
        now = datetime.now(timezone.utc)
        for item in related:
            message = item.error
            if not message and item.status == "skipped" and item.progress_detail:
                message = item.progress_detail
            actions.append(
                JobActionOut(
                    id=item.id,
                    action=getattr(item, "job_kind", None) or "translate",
                    status=item.status,
                    datetime=item.completed_at or item.started_at or item.created_at,
                    duration_seconds=_action_duration_seconds(item, now=now),
                    message=message,
                    current=item.id == job_id,
                )
            )
        return actions

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

    async def get_job_usage(self, job_id: int) -> JobUsageOut | None:
        """Aggregate token/cost stats from the job OpenRouter exchange log."""
        row = self.db.get(JobRow, job_id)
        if row is None:
            return None

        log = self.get_job_log(job_id)
        assert log is not None
        entries = log.entries or []

        pricing_by_model: dict[str, ModelPricing] = {}
        try:
            key, _ = self.settings.get_openrouter_credentials()
            models = await OpenRouterClient.list_models(api_key=key or None)
            for model in models:
                pricing_by_model[model.id] = ModelPricing(
                    name=model.name,
                    prompt_price_per_million=model.prompt_price_per_million,
                    completion_price_per_million=model.completion_price_per_million,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load OpenRouter pricing for job usage: %s", exc)

        exchanges = parse_exchanges(
            entries,
            fallback_model=row.model,
            pricing_by_model=pricing_by_model,
        )
        agg = aggregate_usage(exchanges)

        by_model: list[JobUsageModelOut] = []
        for item in agg["by_model"]:
            pricing = pricing_by_model.get(item["model"])
            by_model.append(
                JobUsageModelOut(
                    model=item["model"],
                    name=pricing.name if pricing else None,
                    requests=item["requests"],
                    input_tokens=item["input_tokens"],
                    output_tokens=item["output_tokens"],
                    total_tokens=item["total_tokens"],
                    cost_usd=item["cost_usd"],
                    prompt_price_per_million=(
                        pricing.prompt_price_per_million if pricing else None
                    ),
                    completion_price_per_million=(
                        pricing.completion_price_per_million if pricing else None
                    ),
                )
            )

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

        related_actions: list[JobUsageRelatedOut] = []
        actions = self.list_job_actions(job_id) or []
        for action in actions:
            related = self.db.get(JobRow, action.id)
            if related is None:
                continue
            related_cost = None
            if related.input_tokens is not None or related.output_tokens is not None:
                related_cost = estimate_cost_usd(
                    input_tokens=related.input_tokens or 0,
                    output_tokens=related.output_tokens or 0,
                    pricing=pricing_by_model.get(related.model),
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

        # Prefer live log totals; fall back to persisted job token columns when log is empty
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
            if totals.input_tokens or totals.output_tokens:
                totals.cost_usd = estimate_cost_usd(
                    input_tokens=totals.input_tokens,
                    output_tokens=totals.output_tokens,
                    pricing=pricing_by_model.get(row.model),
                )
                if totals.cost_usd is not None and totals.total_tokens > 0:
                    totals.blended_cost_per_million = (
                        totals.cost_usd / totals.total_tokens
                    ) * 1_000_000
                if totals.cost_usd is not None:
                    pricing_source = "estimated"

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
        self.db.commit()

        return ClearDataResult(
            deleted=logs_deleted,
            message=f"Cleared {logs_deleted} usage log(s) and reset token totals on {jobs_reset} job(s).",
            details={"logs_deleted": logs_deleted, "jobs_reset": jobs_reset},
        )

    async def create_job(
        self,
        payload: JobCreate,
        *,
        candidate: CandidateOut | None = None,
    ) -> JobOut:
        public = self.settings.get_public()
        _, model = self.settings.get_openrouter_credentials()
        openrouter_key, _ = self.settings.get_openrouter_credentials()
        if not openrouter_key:
            raise OpenRouterError("OpenRouter API key is not configured")

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
                match = await CandidateService(self.db).get_candidate(candidate_key)
            if not match:
                raise ValueError("Candidate not found. Refresh the list and try again.")
            if not match.can_translate or not match.source_subtitle_path:
                raise ValueError(match.reason or "Candidate cannot be translated.")
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
                model=model,
                status="skipped",
                progress=100,
                reason_code="target_exists",
                error="Target subtitle already exists.",
                completed_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return job_to_out(row)

        # Prevent duplicate active jobs for same target
        existing = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "translate",
                JobRow.target_subtitle_path == str(target),
                JobRow.status.in_(["pending", "processing"]),
            )
        )
        if existing:
            return job_to_out(existing)

        src_hash = content_hash(source)
        dkey = dedupe_key(src_hash, target_language, model)
        completed = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "translate",
                JobRow.dedupe_key == dkey,
                JobRow.status == "completed",
            )
        )
        if completed and Path(completed.target_subtitle_path).exists():
            row = JobRow(
                candidate_key=candidate_key,
                job_kind="translate",
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
                model=model,
                status="skipped",
                progress=100,
                reason_code="cache_hit",
                dedupe_key=dkey,
                source_hash=src_hash,
                error="Identical translation already completed.",
                completed_at=utcnow(),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return job_to_out(row)

        row = JobRow(
            candidate_key=candidate_key,
            job_kind="translate",
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
            model=model,
            status="pending",
            progress=0,
            dedupe_key=dkey,
            source_hash=src_hash,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    async def create_extract_job(
        self,
        payload: ExtractCreate,
        *,
        candidate: CandidateOut | None = None,
    ) -> JobOut:
        public = self.settings.get_public()
        match = candidate
        if match is None or match.key != payload.candidate_key:
            match = await CandidateService(self.db).get_candidate(payload.candidate_key)
        if not match:
            raise ValueError("Candidate not found. Refresh the list and try again.")
        if not match.can_extract or match.extract_stream_index is None:
            raise ValueError(match.reason or "No extractable text subtitle track found.")
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
            return job_to_out(row)

        existing = self.db.scalar(
            select(JobRow).where(
                JobRow.job_kind == "extract",
                JobRow.target_subtitle_path == str(output),
                JobRow.status.in_(["pending", "processing"]),
            )
        )
        if existing:
            return job_to_out(existing)

        dkey = hashlib.sha256(
            f"extract|{match.media_path}|{language}|{match.extract_stream_index}".encode()
        ).hexdigest()
        row = JobRow(
            candidate_key=match.key,
            job_kind="extract",
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
            status="pending",
            progress=0,
            progress_detail="Queued for extraction",
            extract_stream_index=match.extract_stream_index,
            dedupe_key=dkey,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    async def create_request_subtitle_job(
        self,
        candidate_key: str,
        language: str | None = None,
        *,
        candidate: CandidateOut | None = None,
    ) -> JobOut:
        public = self.settings.get_public()
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
            return job_to_out(existing)

        dkey = hashlib.sha256(
            f"request|{match.media_type}|{match.media_path}|{code2}".encode()
        ).hexdigest()
        row = JobRow(
            candidate_key=match.key,
            job_kind="request",
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
            except (ValueError, OpenRouterError) as exc:
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
            .order_by(JobRow.created_at.asc())
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
        return row

    def cancel_job(self, job_id: int) -> JobOut:
        row = self.db.get(JobRow, job_id)
        if not row:
            raise ValueError("Job not found")
        if row.status not in {"pending", "processing"}:
            raise ValueError("Only pending or processing jobs can be cancelled")
        row.status = "cancelled"
        row.completed_at = utcnow()
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
            return await self.create_extract_job(ExtractCreate(candidate_key=row.candidate_key))
        if kind == "request":
            if not row.candidate_key:
                raise ValueError("Request job is missing candidate key")
            return await self.create_request_subtitle_job(
                row.candidate_key,
                language=row.source_language,
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
        return await self.create_job(payload)

    async def retry_bazarr_sync(self, job_id: int) -> JobOut:
        row = self.db.get(JobRow, job_id)
        if not row:
            raise ValueError("Job not found")
        if row.status != "completed":
            raise ValueError("Bazarr sync retry is only available for completed jobs")
        try:
            await self._rescan(row)
            row.warning = None
            row.reason_code = None
        except Exception as exc:  # noqa: BLE001
            row.warning = str(exc)
            row.reason_code = "bazarr_rescan_failed"
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return job_to_out(row)

    async def process_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return
        kind = getattr(row, "job_kind", None) or "translate"
        if kind == "extract":
            await self._process_extract_job(job_id)
            return
        if kind == "request":
            await self._process_request_subtitle_job(job_id)
            return
        await self._process_translate_job(job_id)

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
                return

            # Fallback: extract an embedded text track when Bazarr finds nothing.
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
            await extract_text_track(media, track.stream_index, output)
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
            row.progress_detail = "Extracting embedded text track"
            self.db.add(row)
            self.db.commit()

            await extract_text_track(
                row.media_path,
                row.extract_stream_index,
                row.target_subtitle_path,
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
            log.info("Extract job completed job_id=%s path=%s", job_id, current.target_subtitle_path)
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

    async def _process_translate_job(self, job_id: int) -> None:
        row = self.db.get(JobRow, job_id)
        if not row or row.status != "processing":
            return

        log = get_logger("jobs")
        exchange_log: JobOpenRouterExchangeLog | None = None
        try:
            public = self.settings.get_public()
            openrouter_key, model = self.settings.get_openrouter_credentials()
            if not openrouter_key:
                raise OpenRouterError("OpenRouter API key is not configured")

            source_path = Path(row.source_subtitle_path)
            content = source_path.read_text(encoding="utf-8")
            document = parse_srt(content)
            source_validation = validate_source(document)
            if not source_validation.ok:
                raise ValueError(source_validation.error_message)

            resolved_model = row.model or model
            config = get_app_config()
            exchange_log = JobOpenRouterExchangeLog(
                job_openrouter_log_path(config.config_dir, job_id),
                job_id=job_id,
            )
            exchange_log.record(
                {
                    "event": "job_start",
                    "model": resolved_model,
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
            log.info("OpenRouter exchange log job_id=%s path=%s", job_id, exchange_log.path)

            client = OpenRouterClient(openrouter_key, exchange_log=exchange_log)
            service = OpenRouterTranslationService(client)

            current = self.db.get(JobRow, job_id)
            if current:
                current.progress = 5
                current.progress_detail = "Building glossary"
                self.db.add(current)
                self.db.commit()

            glossary = await GlossaryService(self.db).prepare_for_translation(
                client=client,
                model=resolved_model,
                media_type=row.media_type,
                media_title=row.media_title,
                target_language_code=row.target_language,
                target_language_name=public.target_language.name,
                bazarr_series_id=row.bazarr_series_id,
                bazarr_movie_id=row.bazarr_movie_id,
                document=document,
            )
            exchange_log.record(
                {
                    "event": "glossary_ready",
                    "leaf_scope_id": glossary.leaf_scope.id,
                    "leaf_scope_key": glossary.leaf_scope.key,
                    "universe_key": glossary.universe_key,
                    "scope_ids": [s.id for s in glossary.scopes],
                    "term_count": len(glossary.terms),
                    "suggested_new": glossary.suggested_new,
                    "input_tokens": glossary.usage_input_tokens,
                    "output_tokens": glossary.usage_output_tokens,
                    "total_tokens": glossary.usage_total_tokens,
                }
            )

            async def on_progress(done: int, total: int) -> None:
                current = self.db.get(JobRow, job_id)
                if not current or current.status == "cancelled":
                    raise RuntimeError("Job cancelled")
                # Reserve ~5% for glossary prep.
                current.progress = round(5 + (done / total) * 95, 2)
                current.progress_detail = f"{done} / {total} batches"
                self.db.add(current)
                self.db.commit()

            outcome = await service.translate_document(
                document,
                model=resolved_model,
                target_language_code=row.target_language,
                target_language_name=public.target_language.name,
                batch_size=public.batch_size,
                progress_callback=on_progress,
                glossary_terms=glossary.terms,
            )
            outcome.usage.input_tokens += glossary.usage_input_tokens
            outcome.usage.output_tokens += glossary.usage_output_tokens
            outcome.usage.total_tokens += glossary.usage_total_tokens
            if glossary.suggested_new:
                outcome.warnings.append(
                    f"glossary_suggested:{glossary.suggested_new} new term(s) awaiting review"
                )

            current = self.db.get(JobRow, job_id)
            if not current or current.status == "cancelled":
                exchange_log.record({"event": "job_end", "status": "cancelled"})
                return

            target_path = Path(current.target_subtitle_path)
            write_srt_atomic(target_path, outcome.document, overwrite=False)

            current.input_tokens = outcome.usage.input_tokens
            current.output_tokens = outcome.usage.output_tokens
            current.total_tokens = outcome.usage.total_tokens
            current.progress = 100
            current.progress_detail = "Written"
            current.status = "completed"
            current.completed_at = utcnow()
            if outcome.warnings:
                # Markup mismatches are soft warnings; file was still written.
                current.warning = "; ".join(outcome.warnings)
                if any(w.startswith("glossary_suggested:") for w in outcome.warnings) and all(
                    w.startswith("glossary_suggested:") for w in outcome.warnings
                ):
                    current.reason_code = "glossary_review"
                else:
                    current.reason_code = "markup_warning"
            else:
                current.warning = None

            self.db.add(
                TranslationCacheRow(
                    source_hash=current.source_hash or "",
                    target_language=current.target_language,
                    model=current.model,
                    target_subtitle_path=current.target_subtitle_path,
                    job_id=current.id,
                )
            )

            try:
                await self._rescan(current)
            except Exception as exc:  # noqa: BLE001
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
                    "input_tokens": outcome.usage.input_tokens,
                    "output_tokens": outcome.usage.output_tokens,
                    "total_tokens": outcome.usage.total_tokens,
                    "warning": current.warning,
                    "translation_warnings": outcome.warnings,
                }
            )
            log.info("Job completed job_id=%s", job_id)
        except Exception as exc:  # noqa: BLE001
            log.error("Job failed job_id=%s error=%s", job_id, exc)
            current = self.db.get(JobRow, job_id)
            if current and current.status != "cancelled":
                current.status = "failed"
                current.error = _public_error(exc)
                current.reason_code = _reason_code(exc)
                current.completed_at = utcnow()
                self.db.add(current)
                self.db.commit()
            if exchange_log is not None:
                exchange_log.record(
                    {
                        "event": "job_end",
                        "status": "failed",
                        "error": _public_error(exc),
                        "reason_code": _reason_code(exc),
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
            await client.rescan_episode(row.bazarr_episode_id)
        else:
            raise BazarrError("Missing Bazarr media identifiers for rescan")


def _public_error(exc: Exception) -> str:
    if isinstance(exc, OpenRouterError):
        return str(exc)
    if isinstance(exc, BazarrError):
        return str(exc)
    if isinstance(exc, EmbeddedError):
        return str(exc)
    message = str(exc)
    mapping = {
        "Target subtitle already exists": "Target subtitle already exists.",
        "No compatible source": "No compatible source subtitle was found.",
        "validation": "Translation response failed validation.",
        "extractable": "No extractable text subtitle track found.",
        "ffmpeg": "Embedded subtitle extraction failed.",
    }
    for key, value in mapping.items():
        if key.lower() in message.lower():
            return value
    return message


def _reason_code(exc: Exception) -> str:
    if isinstance(exc, OpenRouterError):
        if "validation" in str(exc).lower():
            return "validation_failed"
        if getattr(exc, "status_code", None) == 401:
            return "openrouter_auth"
        return "openrouter_error"
    if isinstance(exc, BazarrError):
        return "bazarr_error"
    if isinstance(exc, EmbeddedError):
        return "extract_failed"
    return "failed"
