"""Turn subtitle cues into speech segments and clean text for TTS."""

from __future__ import annotations

import re

from app.localization.dubbing.models import SpeechSegment
from app.subtitles.models import SubtitleDocument
from app.subtitles.reading import parse_srt_timestamp

TAG_RE = re.compile(r"</?(?:i|b|u)>", flags=re.IGNORECASE)
MUSIC_RE = re.compile(r"[♪♫🎵🎶]+")
SFX_ONLY_RE = re.compile(r"^(?:\([^)]*\)\s*)+$")
SPEAKER_PREFIX_RE = re.compile(r"^(?:[-–—]\s*)*(?P<name>[^:]{1,40}):\s+")
LEADING_DASH_RE = re.compile(r"(?:^|\s)[-–—]\s+")


def _normalized_cue_text(text: str) -> str:
    """Remove subtitle presentation markup before extracting speech metadata."""
    stripped = TAG_RE.sub("", text or "")
    stripped = MUSIC_RE.sub(" ", stripped)
    stripped = stripped.replace("\n", " ")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    stripped = LEADING_DASH_RE.sub(" ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def speaker_id_from_text(text: str) -> str | None:
    """Read a human-labelled subtitle speaker, when the cue provides one."""
    speaker = SPEAKER_PREFIX_RE.match(_normalized_cue_text(text))
    if not speaker:
        return None
    name = re.sub(r"\s+", " ", speaker.group("name")).strip()
    return name or None


def clean_text_for_tts(text: str) -> str:
    """Normalize cue text for TTS. Empty result means skip the cue."""
    stripped = _normalized_cue_text(text)
    speaker = SPEAKER_PREFIX_RE.match(stripped)
    if speaker:
        stripped = stripped[speaker.end() :].strip()
    if not stripped or SFX_ONLY_RE.match(stripped):
        return ""
    return stripped


def speech_segments_from_document(document: SubtitleDocument) -> list[SpeechSegment]:
    """Map subtitle cues to speech segments and retain any labelled speaker identity."""
    segments: list[SpeechSegment] = []
    for block in document.blocks:
        start_ms = parse_srt_timestamp(block.start) or 0
        end_ms = parse_srt_timestamp(block.end) or 0
        text = clean_text_for_tts(block.text)
        if not text or end_ms <= start_ms:
            continue
        segments.append(
            SpeechSegment(
                start=start_ms / 1000.0,
                end=end_ms / 1000.0,
                text=text,
                speaker_id=speaker_id_from_text(block.text),
                source_cues=[block.index],
            )
        )
    return segments
