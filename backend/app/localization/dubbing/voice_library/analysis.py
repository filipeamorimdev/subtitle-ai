"""Episode-wide cue assignment and reference extraction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_app_config

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import EpisodeVoiceCastRow, MediaItemRow, VoiceCharacterRow
from app.integrations.bazarr.paths import apply_path_mapping, is_under_roots, mappings_from_settings
from app.localization.dubbing.dialogue import speaker_id_from_text, speech_segments_from_document
from app.localization.dubbing.voice_cast import _SampledCue, _build_audio_sample
from app.localization.dubbing.voice_library.embeddings import embedding_from_wav
from app.localization.dubbing.voice_library.paths import (
    character_dir,
    relative_reference_path,
    series_voice_dir,
)
from app.localization.dubbing.voice_library.service import VoiceLibraryError, VoiceLibraryService
from app.media.process_runner import ProcessError, run_process_checked
from app.services.settings import SettingsService
from app.subtitles.filenames import find_existing_sidecar
from app.subtitles.parsers.srt import SrtParseError, parse_srt

AUDIO_SAMPLE_RATE = 16_000
MAX_CLIP_DURATION_SECONDS = 5.0


@dataclass(frozen=True)
class ReferenceCandidate:
    character_key: str
    display_name: str
    cue_indices: list[int]
    relative_path: str
    confidence: float | None


class SpeakerAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.library = VoiceLibraryService(db)

    async def assign_episode_cues(self, media: MediaItemRow, *, target_language: str) -> list[EpisodeVoiceCastRow]:
        public = SettingsService(self.db).get_public()
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        media_path = Path(apply_path_mapping(media.path, mappings)) if media.path else None
        if media_path is None or not media_path.exists():
            raise VoiceLibraryError("Media file is not readable on disk.")
        if not is_under_roots(media_path, public.media_roots):
            raise VoiceLibraryError("Media path is outside configured media roots.")
        subtitle_path = find_existing_sidecar(media_path, target_language)
        if subtitle_path is None:
            raise VoiceLibraryError("Create the localized subtitle before assigning voices.")
        try:
            document = parse_srt(subtitle_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SrtParseError) as exc:
            raise VoiceLibraryError("Could not read the localized subtitle.") from exc

        segments = speech_segments_from_document(document)
        self.db.execute(
            delete(EpisodeVoiceCastRow).where(
                EpisodeVoiceCastRow.media_item_id == media.id,
                EpisodeVoiceCastRow.target_language == target_language,
            )
        )
        rows: list[EpisodeVoiceCastRow] = []
        for segment in segments:
            cue_index = segment.source_cues[0] if segment.source_cues else 0
            if cue_index <= 0:
                continue
            label = segment.speaker_id or speaker_id_from_text(segment.text)
            character: VoiceCharacterRow | None = None
            confidence: float | None = None
            status = "unresolved"
            if label:
                character = self.library.upsert_character(
                    media,
                    target_language=target_language,
                    display_name=label,
                )
                status = "assigned"
                confidence = 1.0
            else:
                clip = await self._extract_cue_clip(media_path, segment.start, segment.duration)
                if clip is not None:
                    try:
                        embedding = embedding_from_wav(clip)
                        character, confidence = self.library.match_character_by_embedding(
                            media,
                            target_language=target_language,
                            embedding=embedding,
                        )
                        if character is not None:
                            status = "assigned"
                        else:
                            status = "uncertain"
                    except ValueError:
                        status = "uncertain"
                else:
                    status = "uncertain"
            row = EpisodeVoiceCastRow(
                media_item_id=media.id,
                target_language=target_language,
                cue_index=cue_index,
                character_id=character.id if character is not None else None,
                speaker_label=label,
                confidence=confidence,
                status=status,
            )
            self.db.add(row)
            rows.append(row)
        self.db.commit()
        return rows

    async def build_reference_candidates(
        self,
        media: MediaItemRow,
        *,
        target_language: str,
    ) -> list[ReferenceCandidate]:
        public = SettingsService(self.db).get_public()
        mappings = mappings_from_settings([m.model_dump() for m in public.path_mappings])
        media_path = Path(apply_path_mapping(media.path, mappings)) if media.path else None
        if media_path is None or not media_path.exists():
            raise VoiceLibraryError("Media file is not readable on disk.")
        subtitle_path = find_existing_sidecar(media_path, target_language)
        if subtitle_path is None:
            raise VoiceLibraryError("Create the localized subtitle before extracting references.")
        document = parse_srt(subtitle_path.read_text(encoding="utf-8", errors="replace"))
        segments = speech_segments_from_document(document)
        grouped: dict[str, list] = {}
        for segment in segments:
            label = segment.speaker_id or speaker_id_from_text(segment.text) or "Unknown"
            grouped.setdefault(label, []).append(segment)
        owner = self.library._cast_owner(media)
        series_dir = series_voice_dir(owner.id, slug=owner.title)
        candidates: list[ReferenceCandidate] = []
        for label, group in grouped.items():
            sampled = [
                _SampledCue(
                    index=segment.source_cues[0],
                    start=segment.start,
                    duration=min(segment.duration, MAX_CLIP_DURATION_SECONDS),
                    text=segment.text,
                )
                for segment in group
                if segment.source_cues and segment.duration >= 0.35
            ][:6]
            if not sampled:
                continue
            audio_bytes = await _build_audio_sample(media_path, sampled)
            character = self.library.upsert_character(
                media,
                target_language=target_language,
                display_name=label,
            )
            dest_dir = character_dir(series_dir, character.character_key)
            dest = dest_dir / f"candidate-{sampled[0].index}.wav"
            dest.write_bytes(audio_bytes)
            relative = relative_reference_path(dest)
            candidates.append(
                ReferenceCandidate(
                    character_key=character.character_key,
                    display_name=label,
                    cue_indices=[item.index for item in sampled],
                    relative_path=relative,
                    confidence=None,
                )
            )
        return candidates

    def _cue_clip_cache_dir(self) -> Path:
        path = get_app_config().config_dir / "cache" / "cue-clips"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _extract_cue_clip(self, media_path: Path, start: float, duration: float) -> Path | None:
        if duration <= 0.05:
            return None
        clip_seconds = min(duration, MAX_CLIP_DURATION_SECONDS)
        digest = hashlib.sha256(
            f"{media_path.resolve()}|{start:.3f}|{clip_seconds:.3f}".encode()
        ).hexdigest()[:20]
        cache_dir = self._cue_clip_cache_dir()
        output = cache_dir / f"{digest}.wav"
        if output.is_file() and output.stat().st_size > 44:
            return output
        staging = cache_dir / f".{digest}.tmp.wav"
        try:
            await run_process_checked(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{clip_seconds:.3f}",
                    "-i",
                    str(media_path),
                    "-map",
                    "0:a:0?",
                    "-ac",
                    "1",
                    "-ar",
                    str(AUDIO_SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(staging),
                ],
                timeout_s=30,
                output_paths=[staging],
            )
        except ProcessError:
            staging.unlink(missing_ok=True)
            return None
        if staging.is_file() and staging.stat().st_size > 44:
            staging.replace(output)
            return output
        staging.unlink(missing_ok=True)
        return None
