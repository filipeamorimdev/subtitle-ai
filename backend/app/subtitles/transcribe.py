"""Speech-to-text transcription of media audio into an SRT sidecar."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.integrations.bazarr.paths import is_under_roots
from app.subtitles.filenames import (
    build_external_subtitle_path,
    find_source_srt_beside_media,
    normalize_language_code,
)
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.writer.srt import write_srt_atomic

logger = get_logger("transcribe")

ASR_PROVIDERS = frozenset({"local", "openai", "local_then_openai"})
ASR_LOCAL_MODELS = frozenset({"tiny", "base", "small", "medium", "large-v3", "distil-large-v3"})
DEFAULT_ASR_PROVIDER = "local_then_openai"
DEFAULT_ASR_LOCAL_MODEL = "small"
OPENAI_WHISPER_MODEL = "whisper-1"
OPENAI_MAX_UPLOAD_BYTES = 24 * 1024 * 1024
NO_SPEECH_PROB_THRESHOLD = 0.65
FFMPEG_TIMEOUT = 1800.0
LOCAL_TRANSCRIBE_TIMEOUT = 8 * 3600.0
OPENAI_REQUEST_TIMEOUT = 600.0

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


@dataclass(frozen=True)
class TranscribeGate:
    can_transcribe: bool
    reason: str | None = None
    reason_code: str | None = None
    can_translate: bool = False
    can_extract: bool = False
    has_active_job: bool = False
    media_path: str | None = None


def normalize_asr_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ASR_PROVIDERS else DEFAULT_ASR_PROVIDER


def normalize_asr_local_model(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ASR_LOCAL_MODELS else DEFAULT_ASR_LOCAL_MODEL


def seconds_to_srt_timestamp(value: float) -> str:
    ms = int(round(max(0.0, value) * 1000.0))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


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


async def probe_duration_seconds(media_path: str | Path) -> float | None:
    media = Path(media_path)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except TimeoutError as exc:
        raise TranscribeError(f"ffprobe timed out for {media.name}") from exc
    except FileNotFoundError as exc:
        raise TranscribeError("ffprobe is not installed") from exc
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[:300]
        raise TranscribeError(f"ffprobe failed for {media.name}: {detail or 'unknown error'}")
    try:
        payload = json.loads(stdout.decode("utf-8"))
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
    timeout: float = FFMPEG_TIMEOUT,
) -> Path:
    media = Path(media_path)
    output = Path(output_path)
    if not media.is_file():
        raise TranscribeError("Media file is not readable on disk.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "mp3":
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        raise TranscribeError(f"ffmpeg audio extract timed out for {media.name}") from exc
    except FileNotFoundError as exc:
        raise TranscribeError("ffmpeg is not installed") from exc
    if proc.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise TranscribeError(f"ffmpeg audio extract failed: {detail or 'empty output'}")
    return output


def _whisper_model_cache() -> Path:
    path = get_app_config().config_dir / "whisper-models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def whisper_cpu_threads() -> int:
    """Cap CTranslate2 threads so the API/event loop keeps a core."""
    configured = int(getattr(get_app_config(), "whisper_cpu_threads", 0) or 0)
    if configured > 0:
        return configured
    count = os.cpu_count() or 2
    if count <= 2:
        return 1
    return max(1, count // 2)


def _run_faster_whisper(
    audio_path: str,
    *,
    model_size: str,
    duration: float | None,
    is_cancelled: CancelCheck | None,
    on_progress: Callable[[float, float], None] | None = None,
) -> TranscriptResult:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscribeError("faster-whisper is not installed") from exc

    threads = whisper_cpu_threads()
    logger.info("Loading Whisper model %s (cpu_threads=%s)", model_size, threads)
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        download_root=str(_whisper_model_cache()),
    )
    logger.info("Whisper model loaded; starting transcription (VAD + decode)")
    if on_progress:
        on_progress(0.0, duration or 0.0)
    segments_iter, info = model.transcribe(
        audio_path,
        vad_filter=True,
        beam_size=5,
        word_timestamps=False,
    )
    detected_duration = float(getattr(info, "duration", 0.0) or 0.0) or duration
    total = detected_duration or duration or 0.0
    logger.info(
        "Whisper iterator ready language=%s duration=%.1fs",
        getattr(info, "language", None),
        total or 0.0,
    )
    collected: list[TranscriptSegment] = []
    for item in segments_iter:
        if is_cancelled and is_cancelled():
            raise TranscribeError("Transcription cancelled.")
        end = float(getattr(item, "end", 0.0) or 0.0)
        collected.append(
            TranscriptSegment(
                start=float(getattr(item, "start", 0.0) or 0.0),
                end=end,
                text=str(getattr(item, "text", "") or ""),
                no_speech_prob=float(getattr(item, "no_speech_prob", 0.0) or 0.0),
            )
        )
        if on_progress:
            on_progress(end, total or end)
        if len(collected) == 1:
            logger.info("Whisper first segment at %.1fs", end)
    language = normalize_language_code(getattr(info, "language", None)) or "en"
    return TranscriptResult(
        language=language,
        segments=collected,
        engine=f"faster-whisper:{model_size}",
        duration=detected_duration,
    )


async def transcribe_with_local(
    audio_path: str | Path,
    *,
    model_size: str = DEFAULT_ASR_LOCAL_MODEL,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
) -> TranscriptResult:
    size = normalize_asr_local_model(model_size)
    path = str(audio_path)
    progress = {"done": 0.0, "total": duration or 0.0}

    def on_sync_progress(done: float, total: float) -> None:
        progress["done"] = done
        if total > 0:
            progress["total"] = total

    if on_progress:
        await _maybe_progress(on_progress, 0.0, duration or 0.0)

    stop_pump = asyncio.Event()

    async def pump() -> None:
        last: tuple[float, float] | None = None
        while not stop_pump.is_set():
            current = (progress["done"], progress["total"])
            if on_progress and current != last:
                last = current
                await _maybe_progress(on_progress, current[0], current[1])
            try:
                await asyncio.wait_for(stop_pump.wait(), timeout=1.0)
            except TimeoutError:
                continue
        current = (progress["done"], progress["total"])
        if on_progress and current != last:
            await _maybe_progress(on_progress, current[0], current[1])

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
                "Local Whisper ran out of memory. Try a smaller model or OpenAI fallback."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TranscribeError(f"Local Whisper failed: {exc}") from exc
    finally:
        stop_pump.set()
        if pump_task is not None:
            await pump_task
    if on_progress:
        done = result.duration or duration or 0.0
        await _maybe_progress(on_progress, done, done or 1.0)
    return result


def _segment_chunks(duration: float, file_size: int) -> list[tuple[float, float]]:
    if file_size <= OPENAI_MAX_UPLOAD_BYTES or duration <= 0:
        return [(0.0, duration)]
    # Conservative split so each chunk stays under the upload cap.
    ratio = file_size / OPENAI_MAX_UPLOAD_BYTES
    chunk_count = max(2, math.ceil(ratio) + 1)
    chunk_len = duration / chunk_count
    chunks: list[tuple[float, float]] = []
    start = 0.0
    while start < duration - 0.05:
        end = min(duration, start + chunk_len)
        chunks.append((start, end))
        start = end
    return chunks or [(0.0, duration)]


async def _openai_transcribe_file(
    client: httpx.AsyncClient,
    api_key: str,
    path: Path,
) -> dict[str, Any]:
    with path.open("rb") as handle:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, handle, "audio/mpeg")},
            data={
                "model": OPENAI_WHISPER_MODEL,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
            },
        )
    if response.status_code >= 400:
        detail = response.text[:400]
        raise TranscribeError(f"OpenAI Whisper API failed ({response.status_code}): {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TranscribeError("OpenAI Whisper API returned an unexpected payload.")
    return payload


def _segments_from_openai(payload: dict[str, Any], *, offset: float = 0.0) -> list[TranscriptSegment]:
    raw = payload.get("segments") or []
    segments: list[TranscriptSegment] = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            segments.append(
                TranscriptSegment(
                    start=float(item.get("start") or 0.0) + offset,
                    end=float(item.get("end") or 0.0) + offset,
                    text=str(item.get("text") or ""),
                    no_speech_prob=float(item.get("no_speech_prob") or 0.0),
                )
            )
        return segments
    text = str(payload.get("text") or "").strip()
    if text:
        segments.append(TranscriptSegment(start=offset, end=offset + 1.0, text=text))
    return segments


async def transcribe_with_openai(
    audio_path: str | Path,
    *,
    api_key: str,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
) -> TranscriptResult:
    path = Path(audio_path)
    if not path.is_file():
        raise TranscribeError("Extracted audio is not readable.")
    key = (api_key or "").strip()
    if not key:
        raise TranscribeError("OpenAI API key is not configured.")
    size = path.stat().st_size
    known_duration = duration
    if known_duration is None:
        known_duration = await probe_duration_seconds(path)
    chunks = _segment_chunks(known_duration or 0.0, size)
    collected: list[TranscriptSegment] = []
    language = "en"
    timeout = httpx.Timeout(OPENAI_REQUEST_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if len(chunks) == 1 and size <= OPENAI_MAX_UPLOAD_BYTES:
            if is_cancelled and is_cancelled():
                raise TranscribeError("Transcription cancelled.")
            payload = await _openai_transcribe_file(client, key, path)
            language = normalize_language_code(payload.get("language")) or "en"
            collected.extend(_segments_from_openai(payload))
        else:
            with tempfile.TemporaryDirectory(prefix="subtitle-ai-openai-") as tmp:
                tmp_dir = Path(tmp)
                for index, (start, end) in enumerate(chunks):
                    if is_cancelled and is_cancelled():
                        raise TranscribeError("Transcription cancelled.")
                    part = tmp_dir / f"chunk-{index:03d}.mp3"
                    command = [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{start:.3f}",
                        "-t",
                        f"{max(0.1, end - start):.3f}",
                        "-i",
                        str(path),
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "64k",
                        str(part),
                    ]
                    proc = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT)
                    if proc.returncode != 0 or not part.is_file():
                        detail = (stderr or b"").decode("utf-8", errors="replace")[-300:]
                        raise TranscribeError(f"Failed to split audio for OpenAI: {detail}")
                    payload = await _openai_transcribe_file(client, key, part)
                    language = normalize_language_code(payload.get("language")) or language
                    collected.extend(_segments_from_openai(payload, offset=start))
                    if on_progress:
                        await _maybe_progress(on_progress, end, known_duration or end)
    if on_progress:
        done = known_duration or (collected[-1].end if collected else 0.0)
        await _maybe_progress(on_progress, done, done or 1.0)
    return TranscriptResult(
        language=language,
        segments=collected,
        engine=f"openai:{OPENAI_WHISPER_MODEL}",
        duration=known_duration,
    )


async def _maybe_progress(callback: ProgressCb, done: float, total: float) -> None:
    result = callback(done, total)
    if asyncio.iscoroutine(result):
        await result


async def transcribe_audio(
    audio_path: str | Path,
    *,
    provider: str,
    local_model: str,
    openai_key: str | None,
    duration: float | None = None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
) -> TranscriptResult:
    policy = normalize_asr_provider(provider)
    errors: list[str] = []
    if policy in {"local", "local_then_openai"}:
        try:
            return await transcribe_with_local(
                audio_path,
                model_size=local_model,
                duration=duration,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
            )
        except TranscribeError as exc:
            errors.append(str(exc))
            if policy != "local_then_openai" or not (openai_key or "").strip():
                raise
            logger.warning("Local Whisper failed; trying OpenAI fallback: %s", exc)
    if policy in {"openai", "local_then_openai"}:
        if not (openai_key or "").strip():
            if errors:
                raise TranscribeError(errors[-1])
            raise TranscribeError("OpenAI API key is not configured.")
        return await transcribe_with_openai(
            audio_path,
            api_key=openai_key or "",
            duration=duration,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
        )
    raise TranscribeError(errors[-1] if errors else "No ASR engine is configured.")


async def transcribe_media_to_srt(
    media_path: str | Path,
    output_path: str | Path | None = None,
    *,
    provider: str,
    local_model: str,
    openai_key: str | None,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCb | None = None,
) -> tuple[Path, TranscriptResult]:
    media = Path(media_path)
    duration = await probe_duration_seconds(media)
    policy = normalize_asr_provider(provider)
    with tempfile.TemporaryDirectory(prefix="subtitle-ai-asr-") as tmp:
        tmp_dir = Path(tmp)
        if policy == "openai":
            audio = tmp_dir / "audio.mp3"
            await extract_audio(media, audio, fmt="mp3")
            result = await transcribe_with_openai(
                audio,
                api_key=openai_key or "",
                duration=duration,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
            )
        elif policy == "local":
            audio = tmp_dir / "audio.wav"
            await extract_audio(media, audio, fmt="wav")
            result = await transcribe_with_local(
                audio,
                model_size=local_model,
                duration=duration,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
            )
        else:
            audio = tmp_dir / "audio.wav"
            await extract_audio(media, audio, fmt="wav")
            try:
                result = await transcribe_with_local(
                    audio,
                    model_size=local_model,
                    duration=duration,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
            except TranscribeError as exc:
                if not (openai_key or "").strip():
                    raise
                logger.warning("Local Whisper failed; trying OpenAI fallback: %s", exc)
                mp3 = tmp_dir / "audio.mp3"
                await extract_audio(media, mp3, fmt="mp3")
                result = await transcribe_with_openai(
                    mp3,
                    api_key=openai_key or "",
                    duration=duration,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
        document = segments_to_document(result.segments)
        target = Path(output_path) if output_path else build_external_subtitle_path(media, result.language)
        write_srt_atomic(target, document, overwrite=True)
        return target, result


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

    translate = can_translate
    extract = can_extract
    if translate is None:
        found = find_source_srt_beside_media(
            path, source_languages or ["en"], target_language=target_language
        )
        translate = found is not None
    if translate:
        return TranscribeGate(
            can_transcribe=False,
            reason="A source subtitle is already available.",
            reason_code="has_source",
            can_translate=True,
            can_extract=False,
            media_path=str(path),
        )
    if extract is None:
        from app.subtitles.embedded import pick_extractable_track, probe_subtitle_tracks

        tracks = await probe_subtitle_tracks(path)
        extract = (
            pick_extractable_track(
                tracks, source_languages or ["en"], target_language=target_language
            )
            is not None
        )
    if extract:
        return TranscribeGate(
            can_transcribe=False,
            reason="An extractable embedded subtitle track is available.",
            reason_code="can_extract",
            can_translate=False,
            can_extract=True,
            media_path=str(path),
        )
    return TranscribeGate(
        can_transcribe=True,
        can_translate=False,
        can_extract=False,
        media_path=str(path),
    )


def format_timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"
