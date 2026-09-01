"""faster-whisper ASR provider."""

from __future__ import annotations

import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord
from app.subtitles.filenames import normalize_language_code

logger = get_logger("asr.faster_whisper")

DEFAULT_ASR_LOCAL_MODEL = "small"
ASR_LOCAL_MODELS = frozenset({"tiny", "base", "small", "medium", "large-v3", "distil-large-v3"})
LOCAL_TRANSCRIBE_TIMEOUT = 8 * 3600.0
LOW_CONFIDENCE = 0.5

CancelCheck = Callable[[], bool]
ProgressCb = Callable[[float, float], Awaitable[None] | None]


class TranscribeProviderError(Exception):
    pass


def normalize_asr_local_model(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ASR_LOCAL_MODELS else DEFAULT_ASR_LOCAL_MODEL


def whisper_cpu_threads() -> int:
    configured = int(getattr(get_app_config(), "whisper_cpu_threads", 0) or 0)
    if configured > 0:
        return configured
    count = os.cpu_count() or 2
    if count <= 2:
        return 1
    return max(1, count // 2)


def _model_cache() -> Path:
    path = get_app_config().config_dir / "whisper-models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_faster_whisper(
    audio_path: str,
    *,
    model_size: str,
    language: str | None = None,
    word_timestamps: bool = True,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: Callable[[float, float], None] | None = None,
) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscribeProviderError("faster-whisper is not installed") from exc

    size = normalize_asr_local_model(model_size)
    threads = whisper_cpu_threads()
    logger.info("Loading Whisper model %s (cpu_threads=%s)", size, threads)
    model = WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        download_root=str(_model_cache()),
    )
    if on_progress:
        on_progress(0.0, duration or 0.0)
    kwargs: dict[str, object] = {
        "vad_filter": True,
        "beam_size": 5,
        "word_timestamps": word_timestamps,
    }
    if language:
        kwargs["language"] = language
    segments_iter, info = model.transcribe(audio_path, **kwargs)
    detected_duration = float(getattr(info, "duration", 0.0) or 0.0) or duration
    total = detected_duration or duration or 0.0
    detected = normalize_language_code(getattr(info, "language", None))
    confidence_raw = getattr(info, "language_probability", None)
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence = None
    logger.info(
        "Whisper iterator ready language=%s confidence=%s duration=%.1fs requested=%s",
        detected,
        confidence,
        total or 0.0,
        language,
    )
    collected: list[TranscriptSegment] = []
    warnings: list[str] = []
    if detected is None:
        warnings.append("Language detection did not return a language code.")
    elif confidence is not None and confidence < LOW_CONFIDENCE:
        warnings.append(
            f"Low language confidence ({confidence:.2f}) for detected language {detected}."
        )
        logger.warning(
            "transcription_language_low_confidence detected=%s confidence=%s requested=%s",
            detected,
            confidence,
            language,
        )
    for item in segments_iter:
        if is_cancelled and is_cancelled():
            raise TranscribeProviderError("Transcription cancelled.")
        end = float(getattr(item, "end", 0.0) or 0.0)
        words: list[TranscriptWord] = []
        raw_words = getattr(item, "words", None) or []
        for raw in raw_words:
            text = str(getattr(raw, "word", getattr(raw, "text", "")) or "").strip()
            if not text:
                continue
            words.append(
                TranscriptWord(
                    start=float(getattr(raw, "start", 0.0) or 0.0),
                    end=float(getattr(raw, "end", 0.0) or 0.0),
                    text=text,
                    probability=float(getattr(raw, "probability", 0.0) or 0.0) or None,
                )
            )
        collected.append(
            TranscriptSegment(
                start=float(getattr(item, "start", 0.0) or 0.0),
                end=end,
                text=str(getattr(item, "text", "") or ""),
                no_speech_prob=float(getattr(item, "no_speech_prob", 0.0) or 0.0),
                words=tuple(words),
            )
        )
        if on_progress:
            on_progress(end, total or end)
        if len(collected) == 1:
            logger.info("Whisper first segment at %.1fs", end)
    return Transcript(
        language=detected,
        language_confidence=confidence,
        segments=tuple(collected),
        requested_language=language,
        provider=f"faster-whisper:{size}",
        duration=detected_duration,
        warnings=tuple(warnings),
        metadata={"model": size},
    )


class FasterWhisperProvider:
    name = "faster-whisper"

    def __init__(self, model_size: str = DEFAULT_ASR_LOCAL_MODEL) -> None:
        self.model_size = normalize_asr_local_model(model_size)

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        word_timestamps: bool = True,
        *,
        duration: float | None = None,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCb | None = None,
    ) -> Transcript:
        import asyncio

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
        thread_cancel = threading.Event()

        def thread_is_cancelled() -> bool:
            return thread_cancel.is_set() or bool(is_cancelled and is_cancelled())

        def observe_cancelled_thread(task: asyncio.Task[Transcript]) -> None:
            """Consume the worker result after cooperative cancellation.

            Python cannot kill a running thread.  Keeping an observer avoids an
            unhandled exception and, importantly, the worker receives an event
            that prevents it from returning a transcript into a retried job.
            """

            thread_cancel.set()

            def _consume(done: asyncio.Task[Transcript]) -> None:
                try:
                    done.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.info("Cancelled local Whisper worker ended: %s", exc)

            task.add_done_callback(_consume)

        thread_task = asyncio.create_task(
            asyncio.to_thread(
                run_faster_whisper,
                str(audio_path),
                model_size=self.model_size,
                language=language,
                word_timestamps=word_timestamps,
                duration=duration,
                is_cancelled=thread_is_cancelled,
                on_progress=on_sync_progress if on_progress else None,
            )
        )
        try:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(thread_task),
                    timeout=LOCAL_TRANSCRIBE_TIMEOUT,
                )
            except TimeoutError as exc:
                observe_cancelled_thread(thread_task)
                raise TranscribeProviderError(
                    f"Local Whisper timed out after {int(LOCAL_TRANSCRIBE_TIMEOUT / 60)} minutes."
                ) from exc
            except asyncio.CancelledError:
                observe_cancelled_thread(thread_task)
                raise
            except TranscribeProviderError:
                raise
            except MemoryError as exc:
                raise TranscribeProviderError(
                    "Local Whisper ran out of memory. Try a smaller model."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise TranscribeProviderError(f"Local Whisper failed: {exc}") from exc
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
