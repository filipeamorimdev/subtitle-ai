"""Score and select the best usable localization source for a media file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.localization.artifacts import SourceArtifact, SourceType
from app.subtitles.embedded import EmbeddedTrack
from app.subtitles.content_language import refine_subtitle_language
from app.subtitles.filenames import (
    find_existing_sidecar,
    is_hi_subtitle_filename,
    is_origin_language,
    language_matches,
    languages_compatible,
    normalize_language_code,
    subtitle_stem,
)

logger = get_logger("source_resolver")

# Deterministic scoring. Higher is better.
# Preferred source languages rank local matches; they must not hide a usable
# other-language sidecar/track. That setting is for Bazarr search only.
# Any extractable subtitle therefore outscores transcription.
SCORE_TARGET = 1000.0
SCORE_PREFERRED_EXTERNAL = 92.0
SCORE_OTHER_EXTERNAL = 86.0
SCORE_PREFERRED_EMBEDDED_TEXT = 80.0
SCORE_OTHER_EMBEDDED_TEXT = 74.0
SCORE_PREFERRED_OCR = 62.0
SCORE_OTHER_OCR = 58.0
SCORE_TRANSCRIPT = 55.0
BONUS_DEFAULT_TRACK = 3.0
BONUS_NON_HI = 2.0
PENALTY_UNKNOWN_LANGUAGE = 12.0
PENALTY_LOW_CONFIDENCE = 15.0
PENALTY_FORCED = 4.0
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class SourceCandidate:
    """A discovered source before scoring."""

    type: SourceType
    language: str | None = None
    language_confidence: float | None = None
    path: str | None = None
    stream_index: int | None = None
    title: str | None = None
    forced: bool = False
    hi: bool = False
    default: bool = False
    extractable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredSource:
    candidate: SourceCandidate
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": str(self.candidate.type),
            "language": self.candidate.language,
            "score": self.score,
            "path": self.candidate.path,
            "stream_index": self.candidate.stream_index,
            "reasons": list(self.reasons),
        }


@dataclass
class SourceResolution:
    selected: SourceArtifact | None
    candidates: list[ScoredSource]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected.to_dict() if self.selected else None
        return {
            "selected": selected["type"] if selected else None,
            "language": selected.get("language") if selected else None,
            "score": selected.get("score") if selected else None,
            "reason": self.reason,
            "artifact": selected,
            "candidates": [item.to_dict() for item in self.candidates],
        }


def _is_preferred(language: str | None, preferred: list[str]) -> bool:
    return bool(language) and language_matches(language, preferred)


def score_candidate(
    candidate: SourceCandidate,
    *,
    preferred_languages: list[str],
    target_language: str | None,
) -> ScoredSource:
    reasons: list[str] = []
    preferred = _is_preferred(candidate.language, preferred_languages)
    unknown = not candidate.language
    low_conf = (
        candidate.language_confidence is not None
        and candidate.language_confidence < LOW_CONFIDENCE_THRESHOLD
    )

    if candidate.type is SourceType.TARGET_SUBTITLE:
        score = SCORE_TARGET
        reasons.append("Existing target subtitle")
    elif candidate.type is SourceType.SUBTITLE:
        if preferred:
            score = SCORE_PREFERRED_EXTERNAL
            reasons.append("Preferred source language found as external text subtitle")
        else:
            score = SCORE_OTHER_EXTERNAL
            reasons.append("Other-language external subtitle (usable translation origin)")
        if candidate.hi:
            score -= BONUS_NON_HI
            reasons.append("HI/SDH filename penalty")
        else:
            score += BONUS_NON_HI
    elif candidate.type is SourceType.EMBEDDED_SUBTITLE:
        if preferred:
            score = SCORE_PREFERRED_EMBEDDED_TEXT
            reasons.append("Preferred source language found as embedded text subtitle")
        else:
            score = SCORE_OTHER_EMBEDDED_TEXT
            reasons.append("Other-language embedded text subtitle")
        if candidate.default:
            score += BONUS_DEFAULT_TRACK
            reasons.append("Default embedded track")
        if candidate.forced:
            score -= PENALTY_FORCED
            reasons.append("Forced track penalty")
        if candidate.hi:
            score -= BONUS_NON_HI
        else:
            score += BONUS_NON_HI
    elif candidate.type is SourceType.OCR:
        if preferred:
            score = SCORE_PREFERRED_OCR
            reasons.append("Preferred language embedded image subtitle (OCR)")
        else:
            score = SCORE_OTHER_OCR
            reasons.append("Other-language embedded image subtitle (OCR)")
        if not candidate.extractable:
            score -= 20.0
            reasons.append("OCR not currently extractable")
    elif candidate.type is SourceType.TRANSCRIPT:
        score = SCORE_TRANSCRIPT
        if preferred or not candidate.language:
            reasons.append("Audio transcription fallback")
        else:
            reasons.append("Audio transcription")
            score -= 5.0
    else:
        score = 0.0
        reasons.append("Unknown source type")

    if unknown and candidate.type is not SourceType.TRANSCRIPT:
        score -= PENALTY_UNKNOWN_LANGUAGE
        reasons.append("Unknown language penalty")
    if low_conf:
        score -= PENALTY_LOW_CONFIDENCE
        reasons.append("Low language confidence penalty")
    if target_language and candidate.language and languages_compatible(
        candidate.language, target_language
    ):
        if candidate.type is not SourceType.TARGET_SUBTITLE:
            # Target-language origin is never used as a translation source.
            score = min(score, 1.0)
            reasons.append("Target language is not a translation origin")

    return ScoredSource(candidate=candidate, score=round(score, 2), reasons=tuple(reasons))


def _artifact_from_scored(scored: ScoredSource) -> SourceArtifact:
    cand = scored.candidate
    return SourceArtifact(
        type=str(cand.type),
        language=cand.language,
        language_confidence=cand.language_confidence,
        path=cand.path,
        quality_score=scored.score,
        reason="; ".join(scored.reasons),
        metadata={
            "stream_index": cand.stream_index,
            "title": cand.title,
            "forced": cand.forced,
            "hi": cand.hi,
            "default": cand.default,
            "extractable": cand.extractable,
            **cand.metadata,
        },
    )


def resolve_from_candidates(
    candidates: list[SourceCandidate],
    *,
    preferred_languages: list[str],
    target_language: str | None,
) -> SourceResolution:
    if not candidates:
        return SourceResolution(selected=None, candidates=[], reason="No source candidates")

    scored = [
        score_candidate(
            item,
            preferred_languages=preferred_languages,
            target_language=target_language,
        )
        for item in candidates
    ]
    scored.sort(
        key=lambda item: (
            -item.score,
            str(item.candidate.type),
            item.candidate.stream_index if item.candidate.stream_index is not None else 10_000,
            item.candidate.path or "",
        )
    )
    winner = scored[0]
    artifact = _artifact_from_scored(winner)
    reason = artifact.reason or f"Selected {artifact.type}"
    logger.info(
        "source_resolved selected=%s language=%s score=%s reason=%s candidates=%s",
        artifact.type,
        artifact.language,
        artifact.quality_score,
        reason,
        len(scored),
    )
    for item in scored:
        logger.info(
            "source_candidate type=%s language=%s score=%s path=%s stream=%s reasons=%s",
            item.candidate.type,
            item.candidate.language,
            item.score,
            item.candidate.path,
            item.candidate.stream_index,
            "; ".join(item.reasons),
        )
    return SourceResolution(selected=artifact, candidates=scored, reason=reason)


def list_sidecar_subtitles(media_path: str | Path) -> list[tuple[Path, str | None, bool]]:
    """Return (path, language, hi) for SRT sidecars belonging to this media file."""
    media = Path(media_path)
    directory = media.parent
    if not directory.is_dir():
        return []
    stem = media.stem
    found: list[tuple[Path, str | None, bool]] = []
    for path in sorted(directory.glob("*.srt")):
        if subtitle_stem(path) != stem:
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        lang = refine_subtitle_language(path)
        if lang is None and path.name == f"{stem}.srt":
            found.append((path, None, False))
            continue
        found.append((path, lang, is_hi_subtitle_filename(path)))
    return found


def candidates_from_disk(
    media_path: str | Path,
    *,
    preferred_languages: list[str],
    target_language: str | None,
) -> list[SourceCandidate]:
    media = Path(media_path)
    found: list[SourceCandidate] = []
    if target_language:
        existing = find_existing_sidecar(media, target_language)
        if existing is not None:
            found.append(
                SourceCandidate(
                    type=SourceType.TARGET_SUBTITLE,
                    language=normalize_language_code(target_language) or target_language,
                    path=str(existing),
                    metadata={"role": "target"},
                )
            )
    for path, lang, hi in list_sidecar_subtitles(media):
        if target_language and languages_compatible(lang, target_language):
            continue
        if not is_origin_language(
            lang,
            preferred_languages=preferred_languages,
            target_language=target_language,
            allow_other_languages=True,
            allow_unlabeled=True,
        ):
            continue
        found.append(
            SourceCandidate(
                type=SourceType.SUBTITLE,
                language=lang,
                path=str(path),
                hi=hi,
            )
        )
    return found


def candidates_from_embedded(tracks: list[EmbeddedTrack]) -> list[SourceCandidate]:
    found: list[SourceCandidate] = []
    for track in tracks:
        if track.kind == "text" and track.extractable:
            found.append(
                SourceCandidate(
                    type=SourceType.EMBEDDED_SUBTITLE,
                    language=track.language,
                    stream_index=track.stream_index,
                    title=track.title,
                    forced=track.forced,
                    hi=track.hi,
                    extractable=True,
                    metadata={"codec": track.codec, "kind": track.kind},
                )
            )
        elif track.kind == "image":
            found.append(
                SourceCandidate(
                    type=SourceType.OCR,
                    language=track.language,
                    stream_index=track.stream_index,
                    title=track.title,
                    forced=track.forced,
                    hi=track.hi,
                    extractable=track.extractable,
                    metadata={"codec": track.codec, "kind": track.kind},
                )
            )
    return found


def transcript_candidate(preferred_languages: list[str]) -> SourceCandidate:
    language = normalize_language_code(preferred_languages[0]) if preferred_languages else None
    return SourceCandidate(
        type=SourceType.TRANSCRIPT,
        language=language,
        language_confidence=None,
        extractable=True,
        metadata={"source": "audio"},
    )


class SourceResolver:
    """Discover local sources and pick the best artifact for a localization task."""

    async def resolve(
        self,
        media_path: str | Path,
        *,
        preferred_languages: list[str],
        target_language: str | None,
        embedded_tracks: list[EmbeddedTrack] | None = None,
        include_transcript: bool = True,
    ) -> SourceResolution:
        media = Path(media_path)
        candidates = candidates_from_disk(
            media,
            preferred_languages=preferred_languages,
            target_language=target_language,
        )
        tracks = embedded_tracks
        if tracks is None and media.is_file():
            from app.subtitles.embedded import EmbeddedError, probe_subtitle_tracks

            try:
                tracks = await probe_subtitle_tracks(media)
            except EmbeddedError:
                logger.info("embedded_probe_failed path=%s", media)
                tracks = []
        if tracks:
            for item in candidates_from_embedded(tracks):
                if not is_origin_language(
                    item.language,
                    preferred_languages=preferred_languages,
                    target_language=target_language,
                    allow_other_languages=True,
                    allow_unlabeled=True,
                ):
                    continue
                candidates.append(item)
        if include_transcript and media.is_file():
            candidates.append(transcript_candidate(preferred_languages))
        return resolve_from_candidates(
            candidates,
            preferred_languages=preferred_languages,
            target_language=target_language,
        )

    def to_planner_snapshot(self, resolution: SourceResolution) -> dict[str, Any]:
        """Map a resolution onto the planner's existing snapshot keys."""
        selected = resolution.selected
        snapshot: dict[str, Any] = {
            "can_translate": False,
            "can_extract": False,
            "source_path": None,
            "source_language": None,
            "extract_stream_index": None,
            "target_exists": False,
            "source_type": None,
            "source_score": None,
            "source_reason": resolution.reason,
        }
        if selected is None:
            return snapshot
        snapshot["source_type"] = selected.type
        snapshot["source_score"] = selected.quality_score
        snapshot["source_reason"] = selected.reason
        snapshot["source_language"] = selected.language
        if selected.type == SourceType.TARGET_SUBTITLE:
            snapshot["target_exists"] = True
            snapshot["source_path"] = selected.path
            return snapshot
        if selected.type == SourceType.SUBTITLE:
            snapshot["can_translate"] = True
            snapshot["source_path"] = selected.path
            return snapshot
        if selected.type in {SourceType.EMBEDDED_SUBTITLE, SourceType.OCR}:
            stream = (selected.metadata or {}).get("stream_index")
            extractable = (selected.metadata or {}).get("extractable", True)
            if extractable and stream is not None:
                snapshot["can_extract"] = True
                snapshot["extract_stream_index"] = stream
            return snapshot
        # transcript (or anything else): do not pretend a subtitle source exists
        return snapshot
