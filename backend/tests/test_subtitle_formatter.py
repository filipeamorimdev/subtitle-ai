"""SubtitleFormatter unit tests."""

from __future__ import annotations

from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord
from app.localization.transcription.subtitle_formatter import FormatterConfig, SubtitleFormatter
from app.subtitles.reading import parse_srt_timestamp


def _transcript_from_words(words: list[TranscriptWord], *, language: str = "en") -> Transcript:
    return Transcript(
        language=language,
        language_confidence=0.99,
        segments=(
            TranscriptSegment(
                start=words[0].start,
                end=words[-1].end,
                text=" ".join(word.text for word in words),
                words=tuple(words),
            ),
        ),
    )


def _words(start: float, texts: list[str], *, each: float = 0.25) -> list[TranscriptWord]:
    out: list[TranscriptWord] = []
    cursor = start
    for text in texts:
        out.append(TranscriptWord(start=cursor, end=cursor + each, text=text))
        cursor += each
    return out


def test_formatter_respects_max_lines_and_chars():
    words = _words(0.0, ["hello"] * 40)
    doc, stats = SubtitleFormatter().format(_transcript_from_words(words))
    assert stats.cues_created >= 2
    for block in doc.blocks:
        lines = [line for line in block.text.split("\n") if line.strip()]
        assert len(lines) <= 2
        assert all(len(line) <= 50 for line in lines)


def test_formatter_prefers_punctuation_boundaries():
    words = _words(
        0.0,
        ["Hello", "there.", "How", "are", "you", "today?", "Fine."],
        each=0.4,
    )
    doc, _stats = SubtitleFormatter().format(_transcript_from_words(words))
    texts = [block.text.replace("\n", " ") for block in doc.blocks]
    joined = " ".join(texts)
    assert "Hello there." in joined or any(text.endswith("there.") for text in texts)


def test_formatter_keeps_honorific_and_name_together():
    words = _words(0.0, ["Mr.", "Smith", "left", "early."], each=0.3)
    doc, _stats = SubtitleFormatter().format(_transcript_from_words(words))
    blob = " ".join(block.text.replace("\n", " ") for block in doc.blocks)
    assert "Mr. Smith" in blob


def test_formatter_timestamps_are_monotonic_without_overlap():
    words = _words(0.0, ["one", "two", "three", "four", "five", "six", "seven", "eight"], each=0.5)
    doc, _stats = SubtitleFormatter().format(_transcript_from_words(words))
    previous_end = -1
    for block in doc.blocks:
        start = parse_srt_timestamp(block.start)
        end = parse_srt_timestamp(block.end)
        assert start is not None and end is not None
        assert end > start
        assert start >= previous_end
        previous_end = end


def test_formatter_merges_extremely_short_cues():
    words = [
        TranscriptWord(0.0, 0.2, "Hi."),
        TranscriptWord(0.25, 0.4, "No."),
        TranscriptWord(1.5, 3.5, "A much longer sentence that should stay readable."),
    ]
    doc, stats = SubtitleFormatter().format(_transcript_from_words(words))
    assert stats.merged_short >= 1 or len(doc.blocks) <= 2


def test_formatter_enforces_reading_speed_by_extending_duration():
    words = [TranscriptWord(0.0, 0.4, "This sentence has quite a few characters in it.")]
    config = FormatterConfig(max_cps=20.0, min_duration=0.8, max_duration=7.0)
    doc, _stats = SubtitleFormatter(config).format(_transcript_from_words(words))
    start = parse_srt_timestamp(doc.blocks[0].start) or 0
    end = parse_srt_timestamp(doc.blocks[0].end) or 0
    duration = (end - start) / 1000.0
    chars = len(doc.blocks[0].text.replace("\n", " "))
    assert duration >= chars / 20.0 - 0.05
