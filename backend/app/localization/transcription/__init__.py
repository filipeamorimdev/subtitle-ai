"""Transcription stage: audio selection, ASR providers, chunking, subtitle formatting."""

from typing import Any

__all__ = ["AudioTrackSelector", "SubtitleFormatter", "TranscriptionService"]


def __getattr__(name: str) -> Any:
    if name == "AudioTrackSelector":
        from app.localization.transcription.audio_selector import AudioTrackSelector

        return AudioTrackSelector
    if name == "SubtitleFormatter":
        from app.localization.transcription.subtitle_formatter import SubtitleFormatter

        return SubtitleFormatter
    if name == "TranscriptionService":
        from app.localization.transcription.service import TranscriptionService

        return TranscriptionService
    raise AttributeError(f"module {__name__!r} has no attribute {name}")
