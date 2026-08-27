"""Localization pipeline orchestration. Jobs invoke stages; they do not own media logic."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.jobs.event_log import JobEventLog
from app.localization.artifacts import MediaArtifact
from app.localization.dubbing.pipeline import DubbingPipeline
from app.localization.dubbing.options import DUB_MIX_BACKGROUND_PRESERVED
from app.localization.source_resolver import SourceResolution, SourceResolver
from app.localization.transcription.service import TranscriptionService
from app.subtitles.transcribe import TranscriptResult, transcribe_media_to_srt

logger = get_logger("localization.pipeline")


class LocalizationPipeline:
    """Orchestrates source resolution, subtitle ASR, and dubbing.

    Media intelligence lives in the stage services. This class only sequences
    them and records the decisions.
    """

    def __init__(
        self,
        *,
        source_resolver: SourceResolver | None = None,
        transcription: TranscriptionService | None = None,
        dubbing: DubbingPipeline | None = None,
    ) -> None:
        self.source_resolver = source_resolver or SourceResolver()
        self.transcription = transcription or TranscriptionService()
        self.dubbing = dubbing or DubbingPipeline()

    async def resolve_source(
        self,
        media_path: str | Path,
        *,
        preferred_languages: list[str],
        target_language: str | None,
    ) -> SourceResolution:
        return await self.source_resolver.resolve(
            media_path,
            preferred_languages=preferred_languages,
            target_language=target_language,
        )

    async def transcribe(
        self,
        media_path: str | Path,
        output_path: str | Path | None = None,
        **kwargs: Any,
    ) -> tuple[Path, TranscriptResult]:
        return await transcribe_media_to_srt(media_path, output_path, **kwargs)

    async def dub(
        self,
        *,
        media_path: str | Path,
        source_srt_path: str | Path,
        target_language: str,
        output_path: str | Path,
        voice_model: str | None = None,
        mix_mode: str = DUB_MIX_BACKGROUND_PRESERVED,
        speaker_voice_overrides: dict[str, str] | None = None,
        event_log: JobEventLog,
        is_cancelled: Callable[[], bool],
        on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> MediaArtifact | None:
        return await self.dubbing.run(
            media_path=media_path,
            source_srt_path=source_srt_path,
            target_language=target_language,
            output_path=output_path,
            voice_model=voice_model,
            mix_mode=mix_mode,
            speaker_voice_overrides=speaker_voice_overrides,
            event_log=event_log,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
        )
