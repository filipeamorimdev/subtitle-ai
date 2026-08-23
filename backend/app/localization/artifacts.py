"""Shared localization artifacts passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class SourceType(StrEnum):
    TARGET_SUBTITLE = "target_subtitle"
    SUBTITLE = "subtitle"
    EMBEDDED_SUBTITLE = "embedded_subtitle"
    OCR = "ocr"
    TRANSCRIPT = "transcript"


@dataclass
class Artifact:
    """Base metadata carrier for pipeline outputs."""

    type: str
    language: str | None = None
    language_confidence: float | None = None
    path: str | None = None
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.language:
            payload["language"] = self.language
        if self.language_confidence is not None:
            payload["language_confidence"] = self.language_confidence
        if self.path:
            payload["path"] = self.path
        if self.provider:
            payload["provider"] = self.provider
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass
class SourceArtifact(Artifact):
    type: str = SourceType.SUBTITLE
    quality_score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["score"] = self.quality_score
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass
class TranscriptArtifact(Artifact):
    type: str = "transcript"
    duration: float | None = None
    segment_count: int = 0
    word_count: int = 0
    chunk_count: int = 1
    source_stream: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.duration is not None:
            payload["duration"] = self.duration
        payload["segment_count"] = self.segment_count
        payload["word_count"] = self.word_count
        payload["chunk_count"] = self.chunk_count
        if self.source_stream is not None:
            payload["source_stream"] = self.source_stream
        return payload


@dataclass
class SubtitleArtifact(Artifact):
    type: str = "subtitle"
    cue_count: int = 0
    formatter_adjustments: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["cue_count"] = self.cue_count
        if self.formatter_adjustments:
            payload["formatter_adjustments"] = self.formatter_adjustments
        return payload


@dataclass
class SpeechArtifact(Artifact):
    type: str = "speech"
    segment_count: int = 0
    speaker_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["segment_count"] = self.segment_count
        if self.speaker_ids:
            payload["speaker_ids"] = self.speaker_ids
        return payload


@dataclass
class AudioArtifact(Artifact):
    type: str = "audio"
    duration: float | None = None
    sample_rate: int = 16000
    channels: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.duration is not None:
            payload["duration"] = self.duration
        payload["sample_rate"] = self.sample_rate
        payload["channels"] = self.channels
        return payload


@dataclass
class MediaArtifact(Artifact):
    type: str = "media"
    duration: float | None = None
    audio_streams: int = 0
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.duration is not None:
            payload["duration"] = self.duration
        payload["audio_streams"] = self.audio_streams
        payload["verified"] = self.verified
        return payload


def path_str(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return str(value)
