"""Duration-based audio chunking and overlap transcript merge."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord

logger = get_logger("transcription.chunking")

DEFAULT_CHUNK_DURATION = 20 * 60.0
DEFAULT_OVERLAP = 20.0
DUPLICATE_RATIO = 0.82


@dataclass(frozen=True)
class AudioChunk:
    index: int
    start: float
    end: float
    overlap: float


def plan_chunks(
    duration: float,
    *,
    chunk_duration: float = DEFAULT_CHUNK_DURATION,
    overlap: float = DEFAULT_OVERLAP,
) -> list[AudioChunk]:
    """Plan duration-based chunks with overlap. Never split purely by file size."""
    if duration <= 0:
        return [AudioChunk(index=0, start=0.0, end=0.0, overlap=0.0)]
    if duration <= chunk_duration + overlap:
        return [AudioChunk(index=0, start=0.0, end=duration, overlap=0.0)]

    chunks: list[AudioChunk] = []
    start = 0.0
    index = 0
    while start < duration - 0.05:
        end = min(duration, start + chunk_duration)
        # Last chunk may be a remainder; still include overlap from previous.
        overlap_here = overlap if index > 0 else 0.0
        if index > 0:
            start = max(0.0, start)
        chunks.append(AudioChunk(index=index, start=round(start, 3), end=round(end, 3), overlap=overlap_here))
        if end >= duration:
            break
        start = end - overlap
        index += 1
    logger.info("audio_chunked count=%s duration=%.1f overlap=%.1f", len(chunks), duration, overlap)
    return chunks


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _token_set(text: str) -> set[str]:
    return {part for part in re.findall(r"[a-z0-9']+", _normalize_text(text)) if part}


def _similar(a: str, b: str) -> bool:
    left, right = _normalize_text(a), _normalize_text(b)
    if not left or not right:
        return False
    if left == right:
        return True
    if left in right or right in left:
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if len(shorter) >= 8 and shorter in longer:
            return True
    sa, sb = _token_set(left), _token_set(right)
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return overlap >= DUPLICATE_RATIO


def offset_transcript(transcript: Transcript, offset: float) -> Transcript:
    segments: list[TranscriptSegment] = []
    for segment in transcript.segments:
        words = tuple(
            TranscriptWord(
                start=word.start + offset,
                end=word.end + offset,
                text=word.text,
                probability=word.probability,
            )
            for word in segment.words
        )
        segments.append(
            TranscriptSegment(
                start=segment.start + offset,
                end=segment.end + offset,
                text=segment.text,
                no_speech_prob=segment.no_speech_prob,
                words=words,
            )
        )
    return Transcript(
        language=transcript.language,
        language_confidence=transcript.language_confidence,
        segments=tuple(segments),
        requested_language=transcript.requested_language,
        provider=transcript.provider,
        duration=(transcript.duration + offset) if transcript.duration is not None else None,
        warnings=transcript.warnings,
        metadata=dict(transcript.metadata),
    )


def _segment_in_window(segment: TranscriptSegment, start: float, end: float) -> bool:
    mid = (segment.start + segment.end) / 2.0
    return start - 0.05 <= mid <= end + 0.05 or (segment.start < end and segment.end > start)


def merge_chunk_transcripts(
    chunks: list[tuple[AudioChunk, Transcript]],
) -> Transcript:
    """Offset timestamps and drop duplicated overlap speech."""
    if not chunks:
        return Transcript(language=None, language_confidence=None, segments=())

    kept: list[TranscriptSegment] = []
    language = chunks[0][1].language
    confidence = chunks[0][1].language_confidence
    requested = chunks[0][1].requested_language
    provider = chunks[0][1].provider
    warnings = list(chunks[0][1].warnings)
    duration = 0.0

    for index, (chunk, transcript) in enumerate(chunks):
        shifted = offset_transcript(transcript, chunk.start) if chunk.start else transcript
        language = shifted.language or language
        if shifted.language_confidence is not None:
            confidence = shifted.language_confidence
            warnings.extend(shifted.warnings)
            ends = [duration, chunk.end, *(seg.end for seg in shifted.segments)]
            duration = max(ends)

        if index == 0 or chunk.overlap <= 0:
            kept.extend(shifted.segments)
            continue

        overlap_start = chunk.start
        overlap_end = chunk.start + chunk.overlap
        previous_overlap = [
            seg for seg in kept if _segment_in_window(seg, overlap_start, overlap_end)
        ]
        new_segments: list[TranscriptSegment] = []
        dropped = 0
        for segment in shifted.segments:
            if not _segment_in_window(segment, overlap_start, overlap_end):
                new_segments.append(segment)
                continue
            duplicate = any(_similar(segment.text, prev.text) for prev in previous_overlap)
            if duplicate:
                dropped += 1
                continue
            new_segments.append(segment)
        logger.info(
            "chunk_overlap_merged chunk=%s dropped=%s kept_new=%s overlap=%.1f-%.1f",
            chunk.index,
            dropped,
            len(new_segments),
            overlap_start,
            overlap_end,
        )
        kept.extend(new_segments)

    kept.sort(key=lambda item: (item.start, item.end))
    monotonic: list[TranscriptSegment] = []
    for segment in kept:
        start = segment.start
        end = segment.end
        if monotonic and start < monotonic[-1].end:
            start = monotonic[-1].end
        if end < start:
            continue
        words = tuple(
            TranscriptWord(start=max(start, word.start), end=max(start, word.end), text=word.text, probability=word.probability)
            for word in segment.words
            if word.end >= start
        )
        monotonic.append(
            TranscriptSegment(
                start=start,
                end=end,
                text=segment.text,
                no_speech_prob=segment.no_speech_prob,
                words=words,
            )
        )

    return Transcript(
        language=language,
        language_confidence=confidence,
        segments=tuple(monotonic),
        requested_language=requested,
        provider=provider,
        duration=duration or None,
        warnings=tuple(dict.fromkeys(warnings)),
        metadata={"chunk_count": len(chunks)},
    )
