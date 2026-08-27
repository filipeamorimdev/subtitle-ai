"""Dubbing timing, speech segments, and overlapping timeline tests."""

from __future__ import annotations

import array
import wave
from pathlib import Path

from app.localization.dubbing.dialogue import speaker_id_from_text, speech_segments_from_document
from app.localization.dubbing.models import SpeechSegment
from app.localization.dubbing.timeline import AudioTimeline, CUE_SAMPLE_RATE
from app.localization.dubbing.timing import TimingEngine
from app.subtitles.models import SubtitleBlock, SubtitleDocument


def test_speech_segments_are_not_raw_cues():
    doc = SubtitleDocument(
        format="srt",
        encoding="utf-8",
        blocks=[
            SubtitleBlock(index=1, start="00:00:01,000", end="00:00:02,500", text="John: Hello"),
            SubtitleBlock(index=2, start="00:00:03,000", end="00:00:04,000", text="(door slams)"),
        ],
    )
    segments = speech_segments_from_document(doc)
    assert len(segments) == 1
    assert segments[0].text == "Hello"
    assert segments[0].speaker_id == "John"
    assert segments[0].source_cues == [1]


def test_speaker_label_is_preserved_after_subtitle_markup_is_removed():
    assert speaker_id_from_text("- <i>Bo (voz off):</i> Aquele é o Angus.") == "Bo (voz off)"
    assert speaker_id_from_text("♪ Música ♪") is None


def test_timing_engine_uses_speed_only_within_cap():
    engine = TimingEngine(max_speed=1.2)
    slight = engine.decide(actual=1.15, available=1.0)
    assert slight.action == "speed"
    assert slight.speed <= 1.2
    long = engine.decide(actual=2.0, available=1.0)
    assert long.action == "adapt"
    short = engine.decide(actual=0.5, available=1.0)
    assert short.action == "silence"
    assert short.speed == 1.0


def _write_pcm_wav(path: Path, samples: array.array, *, sample_rate: int = CUE_SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def _read_pcm_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as handle:
        samples = array.array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
        return samples


def test_timeline_mixes_overlapping_speakers(tmp_path):
    john = tmp_path / "john.wav"
    mary = tmp_path / "mary.wav"
    out = tmp_path / "mix.wav"
    _write_pcm_wav(john, array.array("h", [4000] * 800))
    _write_pcm_wav(mary, array.array("h", [8000] * 800))
    timeline = AudioTimeline()
    timeline.add_clip(john, 0.010, speaker_id="A")
    timeline.add_clip(mary, 0.011, speaker_id="B")
    assert timeline.overlap_count >= 1
    timeline.render(out, media_duration_s=0.1)
    mixed = _read_pcm_wav(out)
    start_john = int(round(0.010 * CUE_SAMPLE_RATE))
    overlap_index = int(round(0.011 * CUE_SAMPLE_RATE)) + 10
    assert mixed[start_john + 5] != 0
    assert mixed[overlap_index] != 0
    assert max(abs(sample) for sample in mixed) <= int(32767 * 0.99)


def test_speech_segment_supports_future_speaker_id():
    segment = SpeechSegment(start=1.0, end=2.0, text="Hi", speaker_id="A", source_cues=[1])
    assert segment.speaker_id == "A"
    assert segment.duration == 1.0
