"""Persist and resolve MediaItem rows from MediaRef."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MediaItemRow
from app.media import MediaRef
from app.media.bazarr_provider import BAZARR_PROVIDER_ID


class MediaItemService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, media_id: int) -> MediaItemRow | None:
        return self.db.get(MediaItemRow, media_id)

    def get_by_external(self, provider_id: str, external_id: str) -> MediaItemRow | None:
        return self.db.scalar(
            select(MediaItemRow).where(
                MediaItemRow.provider_id == provider_id,
                MediaItemRow.external_id == external_id,
            )
        )

    def list_items(self, *, limit: int = 100, offset: int = 0) -> list[MediaItemRow]:
        return list(
            self.db.scalars(
                select(MediaItemRow)
                .order_by(MediaItemRow.updated_at.desc())
                .limit(max(1, min(limit, 5000)))
                .offset(max(0, offset))
            ).all()
        )

    def count_items(self) -> int:
        from sqlalchemy import func

        return int(self.db.scalar(select(func.count()).select_from(MediaItemRow)) or 0)

    def upsert_from_ref(self, ref: MediaRef, *, parent_media_id: int | None = None) -> MediaItemRow:
        row = self.get_by_external(ref.provider_id, ref.external_id)
        if row is None:
            row = MediaItemRow(
                provider_id=ref.provider_id,
                external_id=ref.external_id,
                media_type=ref.media_type,
                title=ref.title,
            )
            self.db.add(row)

        row.media_type = ref.media_type
        row.title = ref.title
        row.year = ref.year
        row.path = ref.path
        row.season = ref.season
        row.episode = ref.episode
        row.episode_title = ref.episode_title
        row.bazarr_movie_id = ref.bazarr_movie_id
        row.bazarr_series_id = ref.bazarr_series_id
        row.bazarr_episode_id = ref.bazarr_episode_id
        if parent_media_id is not None:
            row.parent_media_id = parent_media_id
        elif ref.parent_external_id:
            parent = self.get_by_external(ref.provider_id, ref.parent_external_id)
            if parent is not None:
                row.parent_media_id = parent.id
        if ref.metadata:
            row.metadata_json = dict(ref.metadata)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def upsert_from_candidate_fields(
        self,
        *,
        media_type: str,
        title: str,
        path: str | None,
        bazarr_movie_id: int | None,
        bazarr_series_id: int | None,
        bazarr_episode_id: int | None,
    ) -> MediaItemRow:
        if media_type == "movie" and bazarr_movie_id is not None:
            external_id = f"movie:{bazarr_movie_id}"
            ref = MediaRef(
                provider_id=BAZARR_PROVIDER_ID,
                external_id=external_id,
                media_type="movie",
                title=title,
                path=path,
                bazarr_movie_id=bazarr_movie_id,
            )
        elif bazarr_episode_id is not None:
            external_id = f"episode:{bazarr_episode_id}"
            parent = f"series:{bazarr_series_id}" if bazarr_series_id is not None else None
            ref = MediaRef(
                provider_id=BAZARR_PROVIDER_ID,
                external_id=external_id,
                media_type="episode",
                title=title,
                path=path,
                parent_external_id=parent,
                bazarr_series_id=bazarr_series_id,
                bazarr_episode_id=bazarr_episode_id,
            )
        else:
            # Path-only fallback identity (rare; still stable enough for tasks).
            safe_path = (path or title or "unknown").strip()
            external_id = f"path:{safe_path}"
            ref = MediaRef(
                provider_id=BAZARR_PROVIDER_ID,
                external_id=external_id,
                media_type="movie" if media_type == "movie" else "episode",
                title=title,
                path=path,
                bazarr_movie_id=bazarr_movie_id,
                bazarr_series_id=bazarr_series_id,
                bazarr_episode_id=bazarr_episode_id,
            )
        return self.upsert_from_ref(ref)

    def to_ref(self, row: MediaItemRow) -> MediaRef:
        return MediaRef(
            provider_id=row.provider_id,
            external_id=row.external_id,
            media_type=row.media_type,  # type: ignore[arg-type]
            title=row.title,
            year=row.year,
            season=row.season,
            episode=row.episode,
            episode_title=row.episode_title,
            path=row.path,
            parent_external_id=None,
            bazarr_movie_id=row.bazarr_movie_id,
            bazarr_series_id=row.bazarr_series_id,
            bazarr_episode_id=row.bazarr_episode_id,
            metadata=dict(row.metadata_json or {}),
        )
