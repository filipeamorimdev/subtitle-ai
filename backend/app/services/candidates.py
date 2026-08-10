"""Candidate detection from Bazarr wanted lists."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CandidateOut, EmbeddedSubtitleOut
from app.db.models import JobRow
from app.integrations.bazarr.client import BazarrClient, BazarrError, BazarrSubtitle, BazarrWantedItem
from app.integrations.bazarr.paths import apply_path_mapping, mappings_from_settings
from app.services.settings import SettingsService
from app.subtitles.embedded import (
    EmbeddedTrack,
    pick_extractable_track,
    probe_subtitle_tracks,
)
from app.subtitles.filenames import (
    build_external_subtitle_path,
    build_target_subtitle_path,
    detect_language_from_filename,
    find_source_srt_beside_media,
    language_matches,
    languages_compatible,
    normalize_language_code,
    subtitle_belongs_to_media,
)


def to_bazarr_code2(language: str) -> str:
    """Bazarr download endpoints expect ISO 639-1 code2 (e.g. en, pt)."""
    normalized = normalize_language_code(language) or language.strip()
    base = normalized.split("-", 1)[0].strip().lower()
    if len(base) < 2:
        raise ValueError(f"Invalid language code for Bazarr: {language}")
    # Prefer 2-letter codes; common 3-letter aliases already map via normalize_language_code
    if len(base) == 3:
        remapped = normalize_language_code(base)
        if remapped:
            return remapped.split("-", 1)[0].lower()[:2]
    return base[:2]


def candidate_key(media_type: str, media_path: str, target_language: str) -> str:
    raw = f"{media_type}|{media_path}|{target_language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _track_to_out(track: EmbeddedTrack) -> EmbeddedSubtitleOut:
    return EmbeddedSubtitleOut(
        language=track.language,
        codec=track.codec,
        kind=track.kind,  # type: ignore[arg-type]
        extractable=track.extractable,
        stream_index=track.stream_index,
        hi=track.hi,
        forced=track.forced,
        title=track.title,
        source=track.source,
        label=track.label,
    )


def _bazarr_embedded_tracks(
    subtitles: list[BazarrSubtitle],
    source_languages: list[str],
) -> list[EmbeddedTrack]:
    tracks: list[EmbeddedTrack] = []
    for sub in subtitles:
        if sub.path:
            continue
        lang = normalize_language_code(sub.language_code)
        if lang and not language_matches(lang, source_languages):
            # Still show non-source embedded langs as informational badges
            pass
        tracks.append(
            EmbeddedTrack(
                stream_index=None,
                language=lang,
                codec=None,
                kind="unknown",
                extractable=False,
                hi=sub.hi,
                forced=sub.forced,
                title=sub.language_name,
                source="bazarr",
            )
        )
    return tracks


def _merge_embedded(
    bazarr_tracks: list[EmbeddedTrack],
    probed_tracks: list[EmbeddedTrack],
) -> list[EmbeddedTrack]:
    if probed_tracks:
        return probed_tracks
    return bazarr_tracks


class CandidateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = SettingsService(db)

    async def list_candidates(self) -> list[CandidateOut]:
        public = self.settings.get_public()
        bazarr_url, bazarr_key = self.settings.get_bazarr_credentials()
        if not bazarr_url:
            raise BazarrError("Bazarr URL is not configured")

        client = BazarrClient(bazarr_url, bazarr_key)
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        target = public.target_language.code
        source_langs = public.source_languages

        movies = await client.get_wanted_movies()
        episodes = await client.get_wanted_episodes()

        movie_ids: list[int] = []
        for raw in movies:
            rid = raw.get("radarrId", raw.get("radarrid"))
            if rid is not None:
                movie_ids.append(int(rid))
        episode_ids: list[int] = []
        for raw in episodes:
            eid = raw.get("sonarrEpisodeId", raw.get("episodeid"))
            if eid is not None:
                episode_ids.append(int(eid))

        movies_by_id = {
            int(item["radarrId"]): item
            for item in await client.get_movies_by_ids(movie_ids)
            if item.get("radarrId") is not None
        }
        episodes_by_id = {
            int(item["sonarrEpisodeId"]): item
            for item in await client.get_episodes_by_ids(episode_ids)
            if item.get("sonarrEpisodeId") is not None
        }

        items: list[BazarrWantedItem] = []
        for raw in movies:
            rid = raw.get("radarrId", raw.get("radarrid"))
            detail = movies_by_id.get(int(rid)) if rid is not None else None
            items.append(client.normalize_wanted_movie(client.merge_wanted_with_detail(raw, detail)))
        for raw in episodes:
            eid = raw.get("sonarrEpisodeId", raw.get("episodeid"))
            detail = episodes_by_id.get(int(eid)) if eid is not None else None
            items.append(client.normalize_wanted_episode(client.merge_wanted_with_detail(raw, detail)))

        # Active extract / request jobs keyed by candidate_key
        active_extract = {
            row.candidate_key: row.id
            for row in self.db.scalars(
                select(JobRow).where(
                    JobRow.job_kind == "extract",
                    JobRow.status.in_(["pending", "processing"]),
                    JobRow.candidate_key.is_not(None),
                )
            ).all()
            if row.candidate_key
        }
        active_request = {
            row.candidate_key: row.id
            for row in self.db.scalars(
                select(JobRow).where(
                    JobRow.job_kind == "request",
                    JobRow.status.in_(["pending", "processing"]),
                    JobRow.candidate_key.is_not(None),
                )
            ).all()
            if row.candidate_key
        }
        # Most recent job id per candidate (any kind / status)
        latest_job_ids: dict[str, int] = {}
        for row in self.db.scalars(
            select(JobRow)
            .where(JobRow.candidate_key.is_not(None))
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
        ).all():
            if row.candidate_key and row.candidate_key not in latest_job_ids:
                latest_job_ids[row.candidate_key] = row.id

        # Probe media that exists on disk (bounded concurrency)
        paths_to_probe = sorted(
            {
                apply_path_mapping(item.path, mappings)
                for item in items
                if item.path and Path(apply_path_mapping(item.path, mappings)).is_file()
            }
        )
        probed = await self._probe_many(paths_to_probe)

        candidates: list[CandidateOut] = []
        for item in items:
            if not item.path:
                continue
            # Filter to our target language when Bazarr reports missing langs
            missing = item.missing_languages
            if missing and not any(languages_compatible(m, target) for m in missing):
                continue

            local_media = apply_path_mapping(item.path, mappings)
            key = candidate_key(item.media_type, local_media, target)

            source_path: str | None = None
            source_lang: str | None = None

            # Prefer Bazarr subtitle metadata (non-forced, then non-HI, then path order)
            bazarr_sources: list[tuple[int, int, str, str]] = []
            for sub in item.subtitles:
                if not sub.path:
                    continue
                lang = normalize_language_code(sub.language_code) or detect_language_from_filename(
                    sub.path
                )
                if lang and language_matches(lang, source_langs):
                    mapped = apply_path_mapping(sub.path, mappings)
                    if mapped.lower().endswith(".srt") and subtitle_belongs_to_media(
                        mapped, local_media
                    ):
                        bazarr_sources.append(
                            (
                                1 if sub.forced else 0,
                                1 if sub.hi else 0,
                                mapped,
                                lang,
                            )
                        )
            if bazarr_sources:
                bazarr_sources.sort(key=lambda item: (item[0], item[1], item[2]))
                source_path = bazarr_sources[0][2]
                source_lang = bazarr_sources[0][3]

            if source_path is None:
                found = find_source_srt_beside_media(local_media, source_langs)
                if found:
                    path, lang = found
                    source_path = str(path)
                    source_lang = lang

            bazarr_embedded = _bazarr_embedded_tracks(item.subtitles, source_langs)
            embedded_tracks = _merge_embedded(bazarr_embedded, probed.get(local_media, []))
            extract_track = pick_extractable_track(embedded_tracks, source_langs)
            can_extract = extract_track is not None and source_path is None
            # If external source already exists, extraction is unnecessary
            if source_path:
                can_extract = False

            target_path: str | None = None
            can_translate = False
            reason_code: str | None = None
            reason: str | None = None

            media_target = build_external_subtitle_path(local_media, target)
            if source_path:
                target_path = str(
                    build_target_subtitle_path(source_path, target, media_path=local_media)
                )
            elif media_target.exists():
                target_path = str(media_target)

            if target_path and Path(target_path).exists():
                reason_code = "target_exists"
                reason = "Target subtitle already exists."
                can_translate = False
                can_extract = False
            elif media_target.exists():
                # Source-derived path differed, but media-stem target is present
                target_path = str(media_target)
                reason_code = "target_exists"
                reason = "Target subtitle already exists."
                can_translate = False
                can_extract = False
            elif source_path:
                if not Path(source_path).exists():
                    reason_code = "source_missing_on_disk"
                    reason = "Source subtitle path is not readable on disk."
                    can_translate = False
                else:
                    can_translate = True
            else:
                reason_code = "no_source"
                if can_extract:
                    reason = "Embedded text subtitle available — extract to create a source SRT."
                elif any(t.kind == "image" for t in embedded_tracks):
                    reason = "Only image-based embedded subtitles found (not extractable in v0.1)."
                elif embedded_tracks:
                    reason = "Embedded subtitles found, but none are extractable text tracks."
                else:
                    reason = "No compatible source subtitle was found."
                can_translate = False

            candidates.append(
                CandidateOut(
                    key=key,
                    media_type=item.media_type,  # type: ignore[arg-type]
                    title=item.title,
                    media_path=local_media,
                    bazarr_movie_id=item.movie_id,
                    bazarr_episode_id=item.episode_id,
                    bazarr_series_id=item.series_id,
                    target_language=target,
                    source_language=source_lang,
                    source_subtitle_path=source_path,
                    target_subtitle_path=target_path,
                    can_translate=can_translate,
                    reason_code=reason_code,
                    reason=reason,
                    embedded_subtitles=[_track_to_out(t) for t in embedded_tracks],
                    has_embedded=bool(embedded_tracks),
                    can_extract=can_extract,
                    extract_stream_index=extract_track.stream_index if extract_track else None,
                    extract_language=(
                        extract_track.language
                        or (normalize_language_code(source_langs[0]) if source_langs else "en")
                    )
                    if extract_track
                    else None,
                    active_extract_job_id=active_extract.get(key),
                    active_request_job_id=active_request.get(key),
                    latest_job_id=latest_job_ids.get(key),
                )
            )

        candidates.sort(key=lambda c: (c.media_type, c.title.lower()))
        return candidates

    async def get_candidate(self, key: str) -> CandidateOut | None:
        candidates = await self.list_candidates()
        return next((c for c in candidates if c.key == key), None)

    async def _probe_many(self, paths: list[str], *, concurrency: int = 6) -> dict[str, list[EmbeddedTrack]]:
        if not paths:
            return {}
        semaphore = asyncio.Semaphore(concurrency)
        results: dict[str, list[EmbeddedTrack]] = {}

        async def _one(path: str) -> None:
            async with semaphore:
                try:
                    results[path] = await probe_subtitle_tracks(path)
                except Exception:  # noqa: BLE001
                    results[path] = []

        await asyncio.gather(*[_one(path) for path in paths])
        return results
