"""ASR provider protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.localization.transcription.models import Transcript


class ASRProvider(Protocol):
    name: str

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        word_timestamps: bool = True,
        *,
        duration: float | None = None,
        is_cancelled: object | None = None,
        on_progress: object | None = None,
    ) -> Transcript:
        ...
