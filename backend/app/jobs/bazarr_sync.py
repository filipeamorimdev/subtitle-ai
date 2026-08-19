"""Bazarr disk scan plus optional subtitle upload registration."""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.db.models import JobRow
from app.integrations.bazarr.client import BazarrClient, BazarrError

logger = get_logger("jobs.bazarr_sync")


async def register_or_rescan(client: BazarrClient, row: JobRow) -> None:
    """Prefer uploading the sidecar so Bazarr indexes it; fall back to Scan Disk."""
    target = Path(row.target_subtitle_path)
    media_type = row.media_type
    language = row.target_language
    movie_id = row.bazarr_movie_id
    episode_id = row.bazarr_episode_id
    series_id = row.bazarr_series_id
    job_id = row.id
    uploaded = False
    if target.is_file():
        try:
            uploaded = await client.upload_subtitle(
                media_type=media_type,
                language=language,
                path=target,
                movie_id=movie_id,
                episode_id=episode_id,
                series_id=series_id,
            )
        except BazarrError as exc:
            logger.info(
                "Bazarr upload skipped job_id=%s error=%s",
                job_id,
                exc,
            )
    if uploaded:
        logger.info("Bazarr accepted uploaded subtitle job_id=%s path=%s", job_id, target)
        return
    await rescan(client, row)


async def rescan(client: BazarrClient, row: JobRow) -> None:
    media_type = row.media_type
    movie_id = row.bazarr_movie_id
    episode_id = row.bazarr_episode_id
    series_id = row.bazarr_series_id
    if media_type == "movie" and movie_id is not None:
        await client.rescan_movie(movie_id)
    elif episode_id is not None:
        await client.rescan_episode(episode_id, series_id)
    else:
        raise BazarrError("Missing Bazarr media identifiers for rescan")
