"""Speech-to-text transcription of media audio into an SRT sidecar.

Public facade kept for jobs and tests. New pipeline stages live under
``app.localization.transcription``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.integrations.bazarr.paths import is_under_roots
from app.localization.source_resolver import SourceResolver, SourceType
from app.localization.transcription.models import Transcript as StructuredTranscript
from app.localization.transcription.providers.faster_whisper import (
    DEFAULT_ASR_LOCAL_MODEL,
    ASR_LOCAL_MODELS,
    TranscribeProviderError,
    run_faster_whisper as _run_structured_whisper,
)
from app.localization.transcription.service import (
    FFMPEG_TIMEOUT,  # noqa: F401
    TranscriptionError,
    TranscriptionService,
)
from app.localization.transcription.subtitle_formatter import seconds_to_srt_timestamp
from app.subtitles.models import SubtitleBlock, SubtitleDocument

logger = get_logger("transcribe")

# Re-exported for existing imports/tests.
NO_SPEECH_PROB_THRESHOLD = 0.65
LOCAL_TRANSCRIBE_TIMEOUT = 8 * 3600.0

CancelCheck = Callable[[], bool]
ProgressCb = Callable[[float, float], Awaitable[None] | None]


class TranscribeError(Exception):
    pass


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0


@dataclass(frozen=True)
class TranscriptResult:
    language: str
    segments: list[TranscriptSegment]
    engine: str
    duration: float | None = None
    language_confidence: float | None = None
    requested_language: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranscribeGate:
    can_transcribe: bool
    reason: str | None = None
    reason_code: str | None = None
    can_translate: bool = False
    can_extract: bool = False
    has_active_job: bool = False
    media_path: str | None = None
    source_type: str | None = None
    source_score: float | None = None
    source_reason: str | None = None


def normalize_asr_local_model(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ASR_LOCAL_MODELS else DEFAULT_ASR_LOCAL_MODEL


def whisper_cpu_threads() -> int:
    """Cap CTranslate2 threads so the API/event loop keeps a core."""
    configured = int(getattr(get_app_config(), "whisper_cpu_threads", 0) or 0)
    if configured > 0:
        return configured
    count = os.cpu_count() or 2
    if count <= 2:
        return 1
    return max(1, count // 2)


def filter_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    kept: list[TranscriptSegment] = []
    for item in segments:
        text = " ".join((item.text or "").split())
        if not text:
            continue
        if item.no_speech_prob >= NO_SPEECH_PROB_THRESHOLD:
            continue
        end = item.end if item.end > item.start else item.start + 0.5
        kept.append(TranscriptSegment(start=item.start, end=end, text=text, no_speech_prob=item.no_speech_prob))
    return kept


def segments_to_document(segments: list[TranscriptSegment]) -> SubtitleDocument:
    filtered = filter_segments(segments)
    if not filtered:
        raise TranscribeError("Transcription produced no usable speech segments.")
    blocks = [
        SubtitleBlock(
            index=index,
            start=seconds_to_srt_timestamp(item.start),
            end=seconds_to_srt_timestamp(item.end),
            text=item.text,
            original_text=item.text,
        )
        for index, item in enumerate(filtered, start=1)
    ]
    return SubtitleDocument(format="srt", encoding="utf-8", blocks=blocks)


def _legacy_from_structured(transcript: StructuredTranscript) -> TranscriptResult:
    segments = [
        TranscriptSegment(
            start=item.start,
            end=item.end,
            text=item.text,
            no_speech_prob=item.no_speech_prob,
        )
        for item in transcript.segments
    ]
    language = transcript.language or "und"
    return TranscriptResult(
        language=language,
        segments=segments,
        engine=transcript.provider or "unknown",
        duration=transcript.duration,
        language_confidence=transcript.language_confidence,
        requested_language=transcript.requested_language,
        warnings=transcript.warnings,
    )


def _run_faster_whisper(
    audio_path: str,
    *,
    model_size: str,
    duration: float | None,
    is_cancelled: CancelCheck | None,
    on_progress: Callable[[float, float], None] | None = None,
    language: str | None = None,
) -> TranscriptResult:
    try:
        transcript = _run_structured_whisper(
            audio_path,
            model_size=model_size,
            language=language,
            word_timestamps=True,
            duration=duration,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
        )
    except TranscribeProviderError as exc:
        raise TranscribeError(str(exc)) from exc
    return _legacy_from_structured(transcript)


async def transcribe_with_local(
    audio_path: str | Path,
    *,
    model_size: str = DEFAULT_ASR_LOCAL_MODEL,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
    language: str | None = None,
) -> TranscriptResult:
    size = normalize_asr_local_model(model_size)
    path = str(audio_path)
    progress = {"done": 0.0, "total": duration or 0.0}

    def on_sync_progress(done: float, total: float) -> None:
        progress["done"] = done
        if total > 0:
            progress["total"] = total

    async def _maybe_progress(callback: ProgressCb, done: float, total: float) -> None:
        result = callback(done, total)
        if asyncio.iscoroutine(result):
            await result

    if on_progress:
        try:
            await _maybe_progress(on_progress, 0.0, duration or 0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Transcribe progress update failed: %s", exc)

    stop_pump = asyncio.Event()

    async def pump() -> None:
        last: tuple[float, float] | None = None
        while not stop_pump.is_set():
            current = (progress["done"], progress["total"])
            if on_progress and current != last:
                last = current
                try:
                    await _maybe_progress(on_progress, current[0], current[1])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Transcribe progress update failed: %s", exc)
            try:
                await asyncio.wait_for(stop_pump.wait(), timeout=1.0)
            except TimeoutError:
                continue
        current = (progress["done"], progress["total"])
        if on_progress and current != last:
            try:
                await _maybe_progress(on_progress, current[0], current[1])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Transcribe progress update failed: %s", exc)

    pump_task = asyncio.create_task(pump()) if on_progress else None
    try:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _run_faster_whisper,
                    path,
                    model_size=size,
                    duration=duration,
                    is_cancelled=is_cancelled,
                    on_progress=on_sync_progress if on_progress else None,
                    language=language,
                ),
                timeout=LOCAL_TRANSCRIBE_TIMEOUT,
            )
        except TimeoutError as exc:
            raise TranscribeError(
                f"Local Whisper timed out after {int(LOCAL_TRANSCRIBE_TIMEOUT / 60)} minutes."
            ) from exc
        except TranscribeError:
            raise
        except MemoryError as exc:
            raise TranscribeError(
                "Local Whisper ran out of memory. Try a smaller model."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TranscribeError(f"Local Whisper failed: {exc}") from exc
    finally:
        stop_pump.set()
        if pump_task is not None:
            try:
                await pump_task
            except Exception as exc:  # noqa: BLE001
                logger.warning("Transcribe progress pump failed: %s", exc)
    if on_progress:
        done = result.duration or duration or 0.0
        try:
            await _maybe_progress(on_progress, done, done or 1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Transcribe progress update failed: %s", exc)
    return result


async def transcribe_audio(
    audio_path: str | Path,
    *,
    local_model: str,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
    language: str | None = None,
) -> TranscriptResult:
    return await transcribe_with_local(
        audio_path,
        model_size=local_model,
        duration=duration,
        is_cancelled=is_cancelled,
        on_progress=on_progress,
        language=language,
    )


async def transcribe_media_to_srt(
    media_path: str | Path,
    output_path: str | Path | None = None,
    *,
    local_model: str,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
    source_language: str | None = None,
    preferred_languages: list[str] | None = None,
    event_cb: Callable[[str, dict], None] | None = None,
) -> tuple[Path, TranscriptResult]:
    try:
        path, transcript, *_rest = await TranscriptionService().transcribe_media_to_srt(
            media_path,
            output_path,
            local_model=local_model,
            source_language=source_language,
            preferred_languages=preferred_languages,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            event_cb=event_cb,
        )
    except TranscriptionError as exc:
        raise TranscribeError(str(exc)) from exc
    return path, _legacy_from_structured(transcript)


async def assess_transcribe_gate(
    media_path: str | None,
    *,
    media_roots: list[str],
    source_languages: list[str],
    has_active_transcribe: bool,
    can_translate: bool | None = None,
    can_extract: bool | None = None,
    target_language: str | None = None,
) -> TranscribeGate:
    if has_active_transcribe:
        return TranscribeGate(
            can_transcribe=False,
            reason="A transcription job is already running for this media.",
            reason_code="transcribe_active",
            can_translate=bool(can_translate),
            can_extract=bool(can_extract),
            has_active_job=True,
            media_path=media_path,
        )
    if not media_path:
        return TranscribeGate(
            can_transcribe=False,
            reason="Media file path is missing.",
            reason_code="no_media_path",
        )
    path = Path(media_path)
    if not is_under_roots(str(path), media_roots):
        return TranscribeGate(
            can_transcribe=False,
            reason="Media path is outside configured media roots.",
            reason_code="outside_roots",
            media_path=str(path),
        )
    if not path.is_file():
        return TranscribeGate(
            can_transcribe=False,
            reason="Media file is not readable on disk.",
            reason_code="media_missing",
            media_path=str(path),
        )

    preferred = source_languages or ["en"]
    resolution = await SourceResolver().resolve(
        path,
        preferred_languages=preferred,
        target_language=target_language,
    )
    selected = resolution.selected
    source_type = selected.type if selected else None
    source_score = selected.quality_score if selected else None
    source_reason = resolution.reason

    if selected is None or selected.type == SourceType.TRANSCRIPT:
        return TranscribeGate(
            can_transcribe=True,
            can_translate=False,
            can_extract=False,
            media_path=str(path),
            source_type=source_type,
            source_score=source_score,
            source_reason=source_reason,
        )
    if selected.type == SourceType.TARGET_SUBTITLE:
        return TranscribeGate(
            can_transcribe=False,
            reason="A target subtitle is already available.",
            reason_code="target_exists",
            can_translate=True,
            media_path=str(path),
            source_type=source_type,
            source_score=source_score,
            source_reason=source_reason,
        )
    if selected.type == SourceType.SUBTITLE:
        return TranscribeGate(
            can_transcribe=False,
            reason="A source subtitle is already available.",
            reason_code="has_source",
            can_translate=True,
            can_extract=False,
            media_path=str(path),
            source_type=source_type,
            source_score=source_score,
            source_reason=source_reason,
        )
    if selected.type in {SourceType.EMBEDDED_SUBTITLE, SourceType.OCR}:
        return TranscribeGate(
            can_transcribe=False,
            reason="An extractable embedded subtitle track is available.",
            reason_code="can_extract",
            can_translate=False,
            can_extract=True,
            media_path=str(path),
            source_type=source_type,
            source_score=source_score,
            source_reason=source_reason,
        )
    return TranscribeGate(
        can_transcribe=True,
        can_translate=False,
        can_extract=False,
        media_path=str(path),
        source_type=source_type,
        source_score=source_score,
        source_reason=source_reason,
    )


def format_timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"
