"""Job service and worker."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import ExtractCreate, JobCreate, JobOut, StatsOut
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
from app.services.settings import SettingsService
from app.subtitles.embedded import EmbeddedError, extract_text_track
from app.subtitles.filenames import (
    build_external_subtitle_path,
    build_target_subtitle_path,
    language_matches,
    normalize_language_code,
)
from app.subtitles.parsers.srt import parse_srt
from app.subtitles.validation import validate_source
from app.subtitles.writer.srt import write_srt_atomic
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError
from app.translation.openrouter.service import OpenRouterTranslationService

logger = get_logger("jobs")

REQUEST_POLL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 180.0


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


class JobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)

    def list_jobs(self, limit: int = 100) -> list[JobOut]:
        rows = self.db.scalars(
            select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
        ).all()
        return [job_to_out(row) for row in rows]

    def get_job(self, job_id: int) -> JobOut | None:
        row = self.db.get(JobRow, job_id)
        return job_to_out(row) if row else None

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

    async def create_job(self, payload: JobCreate) -> JobOut:
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
            candidates = await CandidateService(self.db).list_candidates()
            match = next((c for c in candidates if c.key == candidate_key), None)
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

        target = build_target_subtitle_path(source, target_language)
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

    async def create_extract_job(self, payload: ExtractCreate) -> JobOut:
        public = self.settings.get_public()
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
    ) -> JobOut:
        public = self.settings.get_public()
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

    def claim_next_job(self) -> JobRow | None:
        row = self.db.scalar(
            select(JobRow)
            .where(JobRow.status == "pending")
            .order_by(JobRow.created_at.asc())
            .limit(1)
        )
        if not row:
            return None
        row.status = "processing"
        row.started_at = utcnow()
        row.progress = 0
        row.progress_detail = "Starting"
        self.db.add(row)
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

            row.progress = 5
            row.progress_detail = f"Asking Bazarr to search for {label}"
            self.db.add(row)
            self.db.commit()

            if row.media_type == "movie":
                if row.bazarr_movie_id is None:
                    raise ValueError("Missing Bazarr movie ID")
                await client.download_movie_subtitle(row.bazarr_movie_id, code2)
            else:
                if row.bazarr_episode_id is None or row.bazarr_series_id is None:
                    raise ValueError("Missing Bazarr series/episode IDs")
                await client.download_episode_subtitle(
                    row.bazarr_series_id,
                    row.bazarr_episode_id,
                    code2,
                )

            row = self.db.get(JobRow, job_id)
            if not row or row.status == "cancelled":
                return
            row.progress = 15
            row.progress_detail = f"Waiting for Bazarr to finish searching for {label}"
            self.db.add(row)
            self.db.commit()

            deadline = monotonic() + REQUEST_TIMEOUT_SECONDS
            found_path: str | None = None
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
                pct = min(90, 15 + int(75 * (elapsed / REQUEST_TIMEOUT_SECONDS)))
                row.progress = pct
                row.progress_detail = f"Still searching for {label} via Bazarr…"
                self.db.add(row)
                self.db.commit()
                await asyncio.sleep(REQUEST_POLL_SECONDS)

            row = self.db.get(JobRow, job_id)
            if not row or row.status == "cancelled":
                return

            if found_path:
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
                log.info("Request job completed job_id=%s path=%s", job_id, found_path)
                return

            row.status = "failed"
            row.progress = 100
            row.progress_detail = f"No {label} subtitle found"
            row.error = (
                f"Bazarr searched for {label} but no external subtitle appeared "
                f"within {int(REQUEST_TIMEOUT_SECONDS)}s."
            )
            row.reason_code = "not_found"
            row.completed_at = utcnow()
            self.db.add(row)
            self.db.commit()
            log.info("Request job finished without subtitle job_id=%s", job_id)
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

            client = OpenRouterClient(openrouter_key)
            service = OpenRouterTranslationService(client)

            async def on_progress(done: int, total: int) -> None:
                current = self.db.get(JobRow, job_id)
                if not current or current.status == "cancelled":
                    raise RuntimeError("Job cancelled")
                current.progress = round((done / total) * 100, 2)
                current.progress_detail = f"{done} / {total} batches"
                self.db.add(current)
                self.db.commit()

            outcome = await service.translate_document(
                document,
                model=row.model or model,
                target_language_code=row.target_language,
                target_language_name=public.target_language.name,
                batch_size=public.batch_size,
                progress_callback=on_progress,
            )

            current = self.db.get(JobRow, job_id)
            if not current or current.status == "cancelled":
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
                current.warning = str(exc)
                current.reason_code = "bazarr_rescan_failed"

            self.db.add(current)
            self.db.commit()
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
