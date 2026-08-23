"""Duration chunking and overlap deduplication tests."""

from __future__ import annotations

from app.localization.transcription.chunking import (
    AudioChunk,
    merge_chunk_transcripts,
    offset_transcript,
    plan_chunks,
)
from app.localization.transcription.models import Transcript, TranscriptSegment


def _seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def _transcript(*segments: TranscriptSegment) -> Transcript:
    return Transcript(language="en", language_confidence=0.9, segments=segments)


def test_plan_chunks_uses_duration_and_overlap():
    chunks = plan_chunks(40 * 60, chunk_duration=20 * 60, overlap=30)
    assert len(chunks) >= 2
    assert chunks[0].start == 0.0
    assert chunks[1].start == 20 * 60 - 30
    assert chunks[1].overlap == 30


def test_offset_transcript_shifts_timestamps():
    original = _transcript(_seg(0.0, 1.5, "Hello"))
    shifted = offset_transcript(original, 1170.0)
    assert shifted.segments[0].start == 1170.0
    assert shifted.segments[0].end == 1171.5


def test_overlap_dedup_drops_repeated_text():
    chunk0 = AudioChunk(index=0, start=0.0, end=120.0, overlap=0.0)
    chunk1 = AudioChunk(index=1, start=100.0, end=220.0, overlap=20.0)
    left = _transcript(
        _seg(90.0, 100.0, "My name is"),
        _seg(100.0, 110.0, "John Smith"),
    )
    right = _transcript(
        _seg(0.0, 10.0, "John Smith"),
        _seg(12.0, 20.0, "and I live here"),
    )
    merged = merge_chunk_transcripts([(chunk0, left), (chunk1, right)])
    texts = [segment.text for segment in merged.segments]
    assert texts.count("John Smith") == 1
    assert "and I live here" in texts
    starts = [segment.start for segment in merged.segments]
    assert starts == sorted(starts)
    for previous, current in zip(merged.segments, merged.segments[1:]):
        assert current.start >= previous.start


def test_sentence_split_across_chunk_boundary_is_not_duplicated():
    chunk0 = AudioChunk(index=0, start=0.0, end=20.0, overlap=0.0)
    chunk1 = AudioChunk(index=1, start=15.0, end=35.0, overlap=5.0)
    left = _transcript(_seg(14.0, 19.5, "Hello my name is"))
    right = _transcript(
        _seg(0.0, 4.0, "Hello my name is"),
        _seg(4.0, 8.0, "John"),
    )
    merged = merge_chunk_transcripts([(chunk0, left), (chunk1, right)])
    texts = [segment.text for segment in merged.segments]
    assert texts.count("Hello my name is") == 1
    assert "John" in texts
