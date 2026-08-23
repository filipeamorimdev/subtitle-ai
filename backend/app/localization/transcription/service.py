"""Transcription orchestration: audio select, extract, chunk, ASR, format."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.logging import get_logger
from app.localization.artifacts import SubtitleArtifact, TranscriptArtifact
from app.localization.transcription.audio_selector import AudioSelection, AudioTrackSelector
from app.localization.transcription.chunking import (
    AudioChunk,
    merge_chunk_transcripts,
    plan_chunks,
)
from app.localization.transcription.models import Transcript
from app.localization.transcription.providers.faster_whisper import (
    FasterWhisperProvider,
    TranscribeProviderError,
)
from app.localization.transcription.providers.openai import OpenAIProvider, OpenAITranscribeError
from app.localization.transcription.subtitle_formatter import SubtitleFormatter
from app.media.process_runner import ProcessError, run_process_checked
from app.subtitles.filenames import build_external_subtitle_path, normalize_language_code
from app.subtitles.writer.srt import write_srt_atomic

logger = get_logger("transcription")

ASR_PROVIDERS = frozenset({"local", "openai", "local_then_openai"})
DEFAULT_ASR_PROVIDER = "local_then_openai"
FFMPEG_TIMEOUT = 1800.0
LOW_CONFIDENCE = 0.5

CancelCheck = Callable[[], bool]
ProgressCb = Callable[[float, float], Awaitable[None] | None]


class TranscriptionError(Exception):
    pass


def normalize_asr_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ASR_PROVIDERS else DEFAULT_ASR_PROVIDER


async def probe_duration_seconds(media_path: str | Path) -> float | None:
    media = Path(media_path)
    try:
        result = await run_process_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(media),
            ],
            timeout_s=60.0,
        )
    except ProcessError as exc:
        raise TranscriptionError(str(exc)) from exc
    try:
        payload = json.loads(result.stdout_text or "{}")
        duration = float((payload.get("format") or {}).get("duration"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


async def extract_audio(
    media_path: str | Path,
    output_path: str | Path,
    *,
    fmt: str = "wav",
    stream_index: int | None = None,
    start: float | None = None,
    duration: float | None = None,
    timeout: float = FFMPEG_TIMEOUT,
    is_cancelled: CancelCheck | None = None,
) -> Path:
    media = Path(media_path)
    output = Path(output_path)
    if not media.is_file():
        raise TranscriptionError("Media file is not readable on disk.")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if start is not None and start > 0:
        command.extend(["-ss", f"{start:.3f}"])
    command.extend(["-i", str(media)])
    if duration is not None and duration > 0:
        command.extend(["-t", f"{duration:.3f}"])
    if stream_index is not None:
        command.extend(["-map", f"0:{stream_index}"])
    command.extend(["-vn", "-ac", "1", "-ar", "16000"])
    if fmt == "mp3":
        command.extend(["-c:a", "libmp3lame", "-b:a", "64k"])
    else:
        command.extend(["-c:a", "pcm_s16le"])
    command.append(str(output))
    try:
        await run_process_checked(
            command,
            timeout_s=timeout,
            is_cancelled=is_cancelled,
            output_paths=[output],
        )
    except ProcessError as exc:
        if exc.outcome.value == "cancelled":
            raise TranscriptionError("Transcription cancelled.") from exc
        raise TranscriptionError(f"ffmpeg audio extract failed: {exc.stderr or exc}") from exc
    if not output.is_file() or output.stat().st_size == 0:
        raise TranscriptionError("ffmpeg audio extract failed: empty output")
    return output


class TranscriptionService:
    def __init__(
        self,
        *,
        selector: AudioTrackSelector | None = None,
        formatter: SubtitleFormatter | None = None,
    ) -> None:
        self.selector = selector or AudioTrackSelector()
        self.formatter = formatter or SubtitleFormatter()

    async def _transcribe_file(
        self,
        audio_path: Path,
        *,
        policy: str,
        local_model: str,
        openai_key: str | None,
        language: str | None,
        duration: float | None,
        is_cancelled: CancelCheck | None,
        on_progress: ProgressCb | None,
        fmt_hint: str,
    ) -> Transcript:
        errors: list[str] = []
        if policy in {"local", "local_then_openai"} and fmt_hint != "openai-only":
            try:
                provider = FasterWhisperProvider(local_model)
                return await provider.transcribe(
                    audio_path,
                    language=language,
                    word_timestamps=True,
                    duration=duration,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
            except TranscribeProviderError as exc:
                errors.append(str(exc))
                if policy != "local_then_openai" or not (openai_key or "").strip():
                    raise TranscriptionError(str(exc)) from exc
                logger.warning("Local Whisper failed; trying OpenAI fallback: %s", exc)
        if policy in {"openai", "local_then_openai"}:
            if not (openai_key or "").strip():
                raise TranscriptionError(errors[-1] if errors else "OpenAI API key is not configured.")
            try:
                provider = OpenAIProvider(openai_key or "")
                return await provider.transcribe(
                    audio_path,
                    language=language,
                    word_timestamps=True,
                    duration=duration,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
            except OpenAITranscribeError as exc:
                raise TranscriptionError(str(exc)) from exc
        raise TranscriptionError(errors[-1] if errors else "No ASR engine is configured.")

    async def transcribe_media_to_srt(
        self,
        media_path: str | Path,
        output_path: str | Path | None = None,
        *,
        provider: str,
        local_model: str,
        openai_key: str | None,
        source_language: str | None = None,
        preferred_languages: list[str] | None = None,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCb | None = None,
        event_cb: Callable[[str, dict], None] | None = None,
    ) -> tuple[Path, Transcript, TranscriptArtifact, SubtitleArtifact, AudioSelection]:
        media = Path(media_path)
        policy = normalize_asr_provider(provider)
        preferred = preferred_languages or ([source_language] if source_language else ["en"])
        duration = await probe_duration_seconds(media)
        selection = await self.selector.select(media, preferred_languages=preferred)
        if event_cb:
            event_cb("audio_selected", selection.to_dict())
        stream_index = selection.selected.stream.stream_index if selection.selected else None
        language_hint = normalize_language_code(source_language) if source_language else None

        import tempfile

        with tempfile.TemporaryDirectory(prefix="subtitle-ai-asr-") as tmp:
            tmp_dir = Path(tmp)
            fmt = "mp3" if policy == "openai" else "wav"
            audio = tmp_dir / f"audio.{fmt}"
            await extract_audio(
                media,
                audio,
                fmt=fmt,
                stream_index=stream_index,
                is_cancelled=is_cancelled,
            )
            chunks = plan_chunks(duration or 0.0)
            if event_cb:
                event_cb("chunks_planned", {"count": len(chunks), "duration": duration})
            logger.info("transcription_chunks count=%s duration=%s", len(chunks), duration)

            async def transcribe_one(chunk: AudioChunk, chunk_audio: Path, progress: ProgressCb | None) -> Transcript:
                chunk_duration = max(0.1, chunk.end - chunk.start)
                return await self._transcribe_file(
                    chunk_audio,
                    policy=policy,
                    local_model=local_model,
                    openai_key=openai_key,
                    language=language_hint,
                    duration=chunk_duration,
                    is_cancelled=is_cancelled,
                    on_progress=progress,
                    fmt_hint="openai-only" if policy == "openai" else "local",
                )

            if len(chunks) == 1:
                transcript = await transcribe_one(chunks[0], audio, on_progress)
                merged = transcript
            else:
                collected: list[tuple[AudioChunk, Transcript]] = []
                for chunk in chunks:
                    if is_cancelled and is_cancelled():
                        raise TranscriptionError("Transcription cancelled.")
                    part_fmt = fmt
                    part = tmp_dir / f"chunk-{chunk.index:03d}.{part_fmt}"
                    await extract_audio(
                        audio if audio.is_file() else media,
                        part,
                        fmt=part_fmt,
                        stream_index=None if audio.is_file() else stream_index,
                        start=chunk.start,
                        duration=max(0.1, chunk.end - chunk.start),
                        is_cancelled=is_cancelled,
                    )

                    async def chunk_progress(done: float, total: float, *, _chunk=chunk) -> None:
                        if not on_progress:
                            return
                        global_done = _chunk.start + done
                        await on_progress(min(global_done, duration or global_done), duration or total)

                    piece = await transcribe_one(chunk, part, chunk_progress if on_progress else None)
                    collected.append((chunk, piece))
                merged = merge_chunk_transcripts(collected)

        warnings = list(merged.warnings)
        if merged.language is None:
            warnings.append("Detected language is unknown; not defaulting to English.")
            logger.warning(
                "transcription_language_unknown requested=%s provider=%s",
                language_hint,
                merged.provider,
            )
        elif merged.language_confidence is not None and merged.language_confidence < LOW_CONFIDENCE:
            logger.warning(
                "transcription_language_low_confidence detected=%s confidence=%s requested=%s",
                merged.language,
                merged.language_confidence,
                language_hint,
            )
        merged = Transcript(
            language=merged.language,
            language_confidence=merged.language_confidence,
            segments=merged.segments,
            requested_language=language_hint,
            provider=merged.provider,
            duration=merged.duration or duration,
            warnings=tuple(warnings),
            metadata={
                **dict(merged.metadata),
                "source_stream": stream_index,
                "audio_selection": selection.to_dict(),
                "chunk_count": len(chunks),
            },
        )

        document, stats = self.formatter.format(merged)
        sidecar_lang = merged.language or language_hint or "und"
        target = Path(output_path) if output_path else build_external_subtitle_path(media, sidecar_lang)
        write_srt_atomic(target, document, overwrite=True)

        transcript_artifact = TranscriptArtifact(
            language=merged.language,
            language_confidence=merged.language_confidence,
            path=str(target),
            provider=merged.provider,
            duration=merged.duration,
            segment_count=len(merged.segments),
            word_count=len(merged.words),
            chunk_count=len(chunks),
            source_stream=stream_index,
            metadata={"requested_language": language_hint, "warnings": list(merged.warnings)},
        )
        subtitle_artifact = SubtitleArtifact(
            language=merged.language,
            path=str(target),
            cue_count=len(document.blocks),
            formatter_adjustments=stats.to_dict(),
        )
        logger.info(
            "transcription_complete language=%s confidence=%s cues=%s stream=%s chunks=%s warnings=%s",
            merged.language,
            merged.language_confidence,
            subtitle_artifact.cue_count,
            stream_index,
            len(chunks),
            list(merged.warnings),
        )
        if event_cb:
            event_cb("transcript_ready", transcript_artifact.to_dict())
            event_cb("subtitle_formatted", subtitle_artifact.to_dict())
        return target, merged, transcript_artifact, subtitle_artifact, selection
