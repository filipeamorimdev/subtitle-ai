"""Structured speech-to-text models independent of SRT."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptWord:
    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True)
class Transcript:
    language: str | None
    language_confidence: float | None
    segments: tuple[TranscriptSegment, ...]
    requested_language: str | None = None
    provider: str | None = None
    duration: float | None = None
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def words(self) -> list[TranscriptWord]:
        collected: list[TranscriptWord] = []
        for segment in self.segments:
            if segment.words:
                collected.extend(segment.words)
                continue
            text = " ".join((segment.text or "").split())
            if not text:
                continue
            collected.append(TranscriptWord(start=segment.start, end=segment.end, text=text))
        return collected
