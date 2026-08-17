"""Bazarr verification for localization tasks.

TaskPlanner owns when verification runs. This service only performs rescan,
target-subtitle presence checks, and maps Bazarr failures to stable reason codes.
Raw Bazarr exceptions stay in logs / execution diagnostics — never as the
user-facing task error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import JobRow, MediaItemRow
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.bazarr.paths import apply_path_mapping, mappings_from_settings
from app.services.settings import SettingsService
from app.subtitles.filenames import (
    build_external_subtitle_path,
    ensure_canonical_sidecar,
    find_existing_sidecar,
)

logger = get_logger("bazarr_verification")

USER_VERIFY_FAILED = "Target subtitle is not yet visible in Bazarr."
USER_RESCAN_FAILED = "Bazarr could not rescan this media."
USER_NOT_CONFIGURED = "Bazarr is not configured."


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    present: bool = False
    reason_code: str | None = None
    message: str | None = None


class BazarrVerificationService:
    """Small Bazarr verify/rescan helper used by TaskPlanner (and job-level retry)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)

    async def verify(self, media: MediaItemRow, target_language: str) -> VerificationResult:
        """Return whether the target subtitle is present and non-empty."""
        self._ensure_canonical_sidecar(media, target_language)
        if not self._local_file_ok(media, target_language):
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_verify_failed",
                message=USER_VERIFY_FAILED,
            )
        present = await self._bazarr_present(media, target_language)
        if not present:
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_verify_failed",
                message=USER_VERIFY_FAILED,
            )
        return VerificationResult(ok=True, present=True)

    async def rescan_and_verify(
        self,
        media: MediaItemRow,
        target_language: str,
    ) -> VerificationResult:
        """Rescan Bazarr, then verify target presence."""
        self._ensure_canonical_sidecar(media, target_language)
        try:
            await self._rescan(media)
        except BazarrError as exc:
            logger.warning(
                "Bazarr rescan failed media=%s error=%s",
                getattr(media, "id", None),
                exc,
            )
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_rescan_failed",
                message=USER_RESCAN_FAILED,
            )
        result = await self.verify(media, target_language)
        if result.ok:
            return result
        return VerificationResult(
            ok=False,
            present=False,
            reason_code=result.reason_code or "bazarr_verify_failed",
            message=result.message or USER_VERIFY_FAILED,
        )

    async def rescan_and_verify_job(self, row: JobRow) -> VerificationResult:
        """Job-row variant for the jobs UI / legacy non-task-backed retry."""
        media_like = _JobMediaAdapter(row)
        ensure_canonical_sidecar(row.target_subtitle_path, row.target_language)
        try:
            await self._rescan(media_like)
        except BazarrError as exc:
            logger.warning("Bazarr rescan failed job=%s error=%s", row.id, exc)
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_rescan_failed",
                message=USER_RESCAN_FAILED,
            )
        if not self._job_file_ok(row):
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_verify_failed",
                message=USER_VERIFY_FAILED,
            )
        present = await self._bazarr_present(media_like, row.target_language)
        if not present:
            return VerificationResult(
                ok=False,
                present=False,
                reason_code="bazarr_verify_failed",
                message=USER_VERIFY_FAILED,
            )
        return VerificationResult(ok=True, present=True)

    def _ensure_canonical_sidecar(
        self,
        media: MediaItemRow | _JobMediaAdapter,
        target_language: str,
    ) -> None:
        path = getattr(media, "path", None)
        if not path:
            return
        public = self.settings.get_public()
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        media_path = Path(apply_path_mapping(path, mappings))
        ensure_canonical_sidecar(media_path, target_language)

    def _local_file_ok(self, media: MediaItemRow | _JobMediaAdapter, target_language: str) -> bool:
        """False only when a local target file exists and is empty. Missing file defers to Bazarr."""
        path = getattr(media, "path", None)
        if not path:
            return True
        public = self.settings.get_public()
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        media_path = Path(apply_path_mapping(path, mappings))
        if not media_path.exists():
            return True
        existing = find_existing_sidecar(media_path, target_language)
        if existing is not None:
            return True
        target = build_external_subtitle_path(media_path, target_language)
        if target.is_file() and target.stat().st_size <= 0:
            return False
        return True

    def _job_file_ok(self, row: JobRow) -> bool:
        existing = find_existing_sidecar(row.target_subtitle_path, row.target_language)
        if existing is not None:
            return True
        target = Path(row.target_subtitle_path)
        if target.is_file() and target.stat().st_size <= 0:
            return False
        return True

    async def _rescan(self, media: MediaItemRow | _JobMediaAdapter) -> None:
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            raise BazarrError(USER_NOT_CONFIGURED)
        client = BazarrClient(bazarr_url, bazarr_key)
        media_type = getattr(media, "media_type", None)
        movie_id = getattr(media, "bazarr_movie_id", None)
        episode_id = getattr(media, "bazarr_episode_id", None)
        series_id = getattr(media, "bazarr_series_id", None)
        if media_type == "movie" and movie_id is not None:
            await client.rescan_movie(movie_id)
        elif episode_id is not None:
            await client.rescan_episode(episode_id, series_id)
        else:
            raise BazarrError("Missing Bazarr media identifiers for rescan")

    async def _bazarr_present(
        self,
        media: MediaItemRow | _JobMediaAdapter,
        target_language: str,
    ) -> bool:
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            return False
        client = BazarrClient(bazarr_url, bazarr_key)
        try:
            media_type = getattr(media, "media_type", None)
            movie_id = getattr(media, "bazarr_movie_id", None)
            episode_id = getattr(media, "bazarr_episode_id", None)
            if media_type == "movie" and movie_id is not None:
                detail = await client.get_movie(movie_id)
            elif episode_id is not None:
                detail = await client.get_episode(episode_id)
            else:
                return False
            return BazarrClient.target_subtitle_present(detail, target_language)
        except BazarrError as exc:
            logger.warning("Bazarr presence check failed error=%s", exc)
            return False


class _JobMediaAdapter:
    """Minimal media-shaped view of a JobRow for rescan/presence."""

    def __init__(self, row: JobRow) -> None:
        self.media_type = row.media_type
        self.path = row.media_path
        self.bazarr_movie_id = row.bazarr_movie_id
        self.bazarr_episode_id = row.bazarr_episode_id
        self.bazarr_series_id = row.bazarr_series_id
        self.id = row.id
