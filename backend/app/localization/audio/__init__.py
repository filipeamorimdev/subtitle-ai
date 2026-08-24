"""Isolated audio stem separation (dialogue vs background).

Not wired into transcription, TTS, translation, or the dubbing mux path.
"""

from typing import Any

__all__ = [
    "AudioSeparationError",
    "AudioSeparationService",
    "DebugTrace",
    "SeparationResult",
    "separate_audio",
]


def __getattr__(name: str) -> Any:
    if name in {"AudioSeparationError", "SeparationResult"}:
        from app.localization.audio.models import AudioSeparationError, SeparationResult

        return AudioSeparationError if name == "AudioSeparationError" else SeparationResult
    if name == "DebugTrace":
        from app.localization.audio.debug import DebugTrace

        return DebugTrace
    if name in {"AudioSeparationService", "separate_audio"}:
        from app.localization.audio.separation import AudioSeparationService, separate_audio

        return AudioSeparationService if name == "AudioSeparationService" else separate_audio
    raise AttributeError(f"module {__name__!r} has no attribute {name}")
