"""Dubbing domain models. Speech is distinct from subtitle cues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceConfig:
    voice_id: str
    language: str
    speaker_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeechSegment:
    start: float
    end: float
    text: str
    speaker_id: str | None = None
    source_cues: list[int] = field(default_factory=list)
    adapted_text: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def spoken_text(self) -> str:
        return (self.adapted_text if self.adapted_text is not None else self.text).strip()
