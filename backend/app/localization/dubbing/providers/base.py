"""TTS provider protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.localization.artifacts import AudioArtifact
from app.localization.dubbing.models import VoiceConfig


class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self,
        text: str,
        voice: VoiceConfig,
        language: str,
        *,
        output_path: Path,
        is_cancelled=None,
    ) -> AudioArtifact:
        ...
