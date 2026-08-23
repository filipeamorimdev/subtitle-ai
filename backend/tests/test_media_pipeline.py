"""Real-media tests using tiny synthetic fixtures (skip if ffmpeg is missing)."""

from __future__ import annotations

import json
import shutil

import pytest

from app.localization.transcription.audio_selector import AudioTrackSelector
from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord
from app.localization.transcription.service import extract_audio
from app.localization.transcription.subtitle_formatter import SubtitleFormatter
from app.subtitles.reading import parse_srt_timestamp
from app.subtitles.writer.srt import render_srt
from app.media.process_runner import run_process_checked
from tests.fixtures.media import build_multitrack_mkv, ffmpeg_available, write_sine_wav


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


@pytest.mark.asyncio
async def test_audio_selector_picks_english_default(tmp_path):
    mkv = build_multitrack_mkv(tmp_path / "multitrack_sample.mkv", tmp_path / "parts")
    selection = await AudioTrackSelector().select(mkv, preferred_languages=["en"])
    assert selection.selected is not None
    assert selection.selected.stream.language in {"en", "eng"}
    assert selection.selected.stream.comment is False
    assert "commentary" not in (selection.selected.stream.title or "").lower()


@pytest.mark.asyncio
async def test_extract_selected_stream_then_format_srt(tmp_path, monkeypatch):
    mkv = build_multitrack_mkv(tmp_path / "movie.mkv", tmp_path / "parts")
    selection = await AudioTrackSelector().select(mkv, preferred_languages=["en"])
    assert selection.selected is not None
    wav = tmp_path / "english_dialogue.wav"
    await extract_audio(mkv, wav, stream_index=selection.selected.stream.stream_index)
    assert wav.is_file() and wav.stat().st_size > 0

    transcript = Transcript(
        language="en",
        language_confidence=0.96,
        requested_language="en",
        provider="fake",
        duration=2.0,
        segments=(
            TranscriptSegment(
                start=0.2,
                end=1.8,
                text="Hello from the default English track",
                words=(
                    TranscriptWord(0.2, 0.5, "Hello"),
                    TranscriptWord(0.5, 0.8, "from"),
                    TranscriptWord(0.8, 1.0, "the"),
                    TranscriptWord(1.0, 1.3, "default"),
                    TranscriptWord(1.3, 1.5, "English"),
                    TranscriptWord(1.5, 1.8, "track"),
                ),
            ),
        ),
    )
    document, stats = SubtitleFormatter().format(transcript)
    assert stats.cues_created >= 1
    previous_end = -1
    for block in document.blocks:
        start = parse_srt_timestamp(block.start) or 0
        end = parse_srt_timestamp(block.end) or 0
        assert end > start
        assert start >= previous_end
        assert len(block.text.split("\n")) <= 2
        previous_end = end
    srt = render_srt(document)
    assert "-->" in srt


@pytest.mark.asyncio
async def test_ffprobe_validates_generated_wav(tmp_path):
    wav = write_sine_wav(tmp_path / "overlapping_speech.wav", duration_s=1.0, frequency=440)
    result = await run_process_checked(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration:format=duration",
            "-of",
            "json",
            str(wav),
        ],
        timeout_s=30,
    )
    payload = json.loads(result.stdout_text)
    assert payload.get("streams")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    assert 0.5 <= duration <= 1.5


def test_ffmpeg_binary_present():
    assert shutil.which("ffmpeg")
    assert shutil.which("ffprobe")
