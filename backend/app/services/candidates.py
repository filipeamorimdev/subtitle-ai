"""Candidate detection from Bazarr wanted lists."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.api.schemas import CandidateOut
from app.integrations.bazarr.client import BazarrClient, BazarrError, BazarrWantedItem
from app.integrations.bazarr.paths import apply_path_mapping, mappings_from_settings
from app.services.settings import SettingsService
from app.subtitles.filenames import (
    build_target_subtitle_path,
    detect_language_from_filename,
    find_source_srt_beside_media,
    language_matches,
    languages_compatible,
    normalize_language_code,
)


def candidate_key(media_type: str, media_path: str, target_language: str) -> str:
    raw = f"{media_type}|{media_path}|{target_language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


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

            # Prefer Bazarr subtitle metadata
            for sub in item.subtitles:
                if not sub.path:
                    continue
                lang = normalize_language_code(sub.language_code) or detect_language_from_filename(
                    sub.path
                )
                if lang and language_matches(lang, source_langs):
                    mapped = apply_path_mapping(sub.path, mappings)
                    if mapped.lower().endswith(".srt"):
                        source_path = mapped
                        source_lang = lang
                        break

            if source_path is None:
                found = find_source_srt_beside_media(local_media, source_langs)
                if found:
                    path, lang = found
                    source_path = str(path)
                    source_lang = lang

            target_path: str | None = None
            can_translate = False
            reason_code: str | None = None
            reason: str | None = None

            if source_path:
                target_path = str(build_target_subtitle_path(source_path, target))
                if Path(target_path).exists():
                    reason_code = "target_exists"
                    reason = "Target subtitle already exists."
                    can_translate = False
                elif not Path(source_path).exists():
                    reason_code = "source_missing_on_disk"
                    reason = "Source subtitle path is not readable on disk."
                    can_translate = False
                else:
                    can_translate = True
            else:
                reason_code = "no_source"
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
                )
            )

        candidates.sort(key=lambda c: (c.media_type, c.title.lower()))
        return candidates
