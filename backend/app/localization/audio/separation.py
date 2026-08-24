"""Orchestrate stream selection, input preparation, stem separation, and verification."""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import uuid
import wave
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.localization.artifacts import AudioArtifact, path_str
from app.localization.audio.debug import DebugTrace, open_debug_trace, run_logged_process
from app.localization.audio.models import (
    CODE_CANCELLED,
    CODE_INPUT_PREPARATION_FAILED,
    CODE_INVALID_OUTPUT,
    CODE_MISSING_BACKGROUND,
    CODE_MISSING_DIALOGUE,
    CODE_SEPARATION_FAILED,
    CODE_VERIFICATION_FAILED,
    DEBUG_FEATURE,
    FEATURE_NAME,
    ROLE_BACKGROUND,
    ROLE_DIALOGUE,
    AudioFileInfo,
    AudioSeparationError,
    AudioSeparationProvider,
    CancelCheck,
    SeparationResult,
    duration_tolerance_s,
)
from app.localization.audio.providers.demucs import DemucsProvider
from app.localization.transcription.audio_selector import (
    AudioSelection,
    AudioStream,
    AudioTrackSelector,
    ScoredAudioStream,
)
from app.media.process_runner import ProcessError, ProcessOutcome

logger = get_logger("audio_separation")

PREPARE_TIMEOUT_S = 1800.0
EventCb = Callable[[str, dict[str, Any]], None]


def _new_task_id(task_id: str | None) -> str:
    raw = (task_id or "").strip()
    if raw:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in raw)
        return safe[:80] or uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:10]}"


def is_pcm_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnchannels() >= 1 and handle.getframerate() > 0 and handle.getnframes() > 0
    except (wave.Error, EOFError, OSError):
        return False


def probe_wav(path: Path) -> AudioFileInfo | None:
    media = Path(path)
    exists = media.is_file()
    size = media.stat().st_size if exists else 0
    if not exists or size <= 0:
        return None
    try:
        with wave.open(str(media), "rb") as handle:
            channels = handle.getnchannels()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
        if channels < 1 or sample_rate < 1 or frames < 1:
            return None
        duration = frames / float(sample_rate)
        return AudioFileInfo(
            path=media,
            exists=True,
            size=size,
            has_audio=True,
            duration=duration,
            sample_rate=sample_rate,
            channels=channels,
        )
    except (wave.Error, EOFError, OSError):
        return None


async def probe_audio(
    path: Path,
    *,
    debug: DebugTrace | None = None,
    is_cancelled: CancelCheck | None = None,
) -> AudioFileInfo:
    media = Path(path)
    exists = media.is_file()
    size = media.stat().st_size if exists else 0
    empty = AudioFileInfo(path=media, exists=exists, size=size, has_audio=False)
    if not exists or size <= 0:
        return empty
    wav_info = probe_wav(media)
    if wav_info is not None:
        return wav_info
    try:
        result = await run_logged_process(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,channels,sample_rate,duration:format=duration",
                "-of",
                "json",
                str(media),
            ],
            debug=debug,
            stage="probe",
            timeout_s=60.0,
            is_cancelled=is_cancelled,
        )
    except ProcessError:
        return empty
    if result.outcome is ProcessOutcome.CANCELLED:
        raise AudioSeparationError(
            "Audio separation cancelled.",
            code=CODE_CANCELLED,
            stage="probe",
        )
    if not result.ok:
        return empty
    try:
        payload = json.loads(result.stdout_text or "{}")
    except json.JSONDecodeError:
        return empty
    streams = payload.get("streams") if isinstance(payload, dict) else None
    audio_streams = [
        item
        for item in streams or []
        if isinstance(item, dict) and str(item.get("codec_type") or "audio").lower() in {"audio", ""}
    ]
    duration = None
    sample_rate = None
    channels = None
    if audio_streams:
        first = audio_streams[0]
        try:
            sample_rate = int(first.get("sample_rate") or 0) or None
        except (TypeError, ValueError):
            sample_rate = None
        try:
            channels = int(first.get("channels") or 0) or None
        except (TypeError, ValueError):
            channels = None
        for raw in (first.get("duration"), (payload.get("format") or {}).get("duration")):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                duration = value
                break
    if duration is None:
        try:
            value = float((payload.get("format") or {}).get("duration"))
            if math.isfinite(value) and value > 0:
                duration = value
        except (TypeError, ValueError):
            duration = None
    return AudioFileInfo(
        path=media,
        exists=True,
        size=size,
        has_audio=bool(audio_streams),
        duration=duration,
        sample_rate=sample_rate,
        channels=channels,
    )


def _raise_if_cancelled(is_cancelled: CancelCheck | None, *, stage: str) -> None:
    if is_cancelled and is_cancelled():
        raise AudioSeparationError(
            "Audio separation cancelled.",
            code=CODE_CANCELLED,
            stage=stage,
        )


def _wav_selection(path: Path, info: AudioFileInfo) -> AudioSelection:
    stream = AudioStream(
        stream_index=0,
        language=None,
        channels=info.channels or 0,
        sample_rate=info.sample_rate,
        default=True,
        codec="pcm_s16le",
    )
    scored = ScoredAudioStream(
        stream=stream,
        score=40.0,
        reasons=("Standalone WAV input", "Single audio stream"),
    )
    return AudioSelection(
        selected=scored,
        candidates=[scored],
        reason="Standalone WAV; default single audio stream",
    )


def _emit(
    event_cb: EventCb | None,
    name: str,
    payload: dict[str, Any],
    *,
    debug: DebugTrace | None = None,
) -> None:
        logger.info("%s %s", name, " ".join(f"{key}={payload[key]}" for key in list(payload)[:6]))
        if event_cb:
            event_cb(name, payload)
        if debug:
            extra = {key: value for key, value in payload.items() if key != "stage"}
            debug.event(str(payload.get("stage") or name), name.replace("_", " "), **extra)


def _unlink(path: Path, debug: DebugTrace | None, reason: str) -> None:
    try:
        if path.is_file():
            path.unlink()
            if debug:
                debug.event("cleanup", reason, path=str(path))
    except OSError:
        logger.warning("Could not remove partial output %s", path)


class AudioSeparationService:
    def __init__(
        self,
        *,
        provider: AudioSeparationProvider | None = None,
        selector: AudioTrackSelector | None = None,
    ) -> None:
        self.provider = provider or DemucsProvider()
        self.selector = selector or AudioTrackSelector()

    async def separate(
        self,
        input_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        task_id: str | None = None,
        job_id: int | str | None = None,
        preferred_languages: list[str] | None = None,
        stream_index: int | None = None,
        start: float | None = None,
        duration: float | None = None,
        is_cancelled: CancelCheck | None = None,
        event_cb: EventCb | None = None,
        debug: bool | None = None,
    ) -> SeparationResult:
        media = Path(input_path)
        config = get_app_config()
        run_id = _new_task_id(task_id)
        trace_enabled = config.debug_trace if debug is None else bool(debug)
        trace = open_debug_trace(feature=DEBUG_FEATURE, task_id=run_id, enabled=trace_enabled, config=config)
        dest = Path(output_dir) if output_dir else config.config_dir / "audio-separation" / run_id
        dest.mkdir(parents=True, exist_ok=True)

        if trace:
            logger.info("audio_separation_started debug_trace=%s", trace.path)
            trace.event(
                "config",
                "separation configuration",
                feature=FEATURE_NAME,
                debug_enabled=True,
                provider=getattr(self.provider, "name", type(self.provider).__name__),
                model=getattr(self.provider, "model", None),
                output_dir=str(dest),
                job_id=job_id,
            )
        else:
            logger.info("audio_separation_started debug_trace=disabled")

        _emit(
            event_cb,
            "audio_separation_started",
            {
                "input_path": str(media),
                "output_dir": str(dest),
                "task_id": run_id,
                "debug_trace_path": str(trace.path) if trace else None,
            },
            debug=trace,
        )

        try:
            _raise_if_cancelled(is_cancelled, stage="start")
            if not media.is_file():
                raise AudioSeparationError(
                    f"Input is not readable: {media}",
                    code=CODE_INPUT_PREPARATION_FAILED,
                    stage="input",
                )
            selection, input_type = await self._select_stream(
                media,
                preferred_languages=preferred_languages or [],
                stream_index=stream_index,
                debug=trace,
                is_cancelled=is_cancelled,
            )
            selected = selection.selected.stream if selection.selected else None
            source_probe = await probe_audio(media, debug=trace, is_cancelled=is_cancelled)
            _emit(
                event_cb,
                "audio_separation_input_selected",
                {
                    "input_path": str(media),
                    "input_type": input_type,
                    "stream_index": selected.stream_index if selected else None,
                    "language": selected.language if selected else None,
                    "channels": selected.channels if selected else None,
                    "sample_rate": selected.sample_rate if selected else source_probe.sample_rate,
                    "duration": source_probe.duration,
                    "reason": selection.reason,
                },
                debug=trace,
            )
            if trace:
                trace.decision(
                    f"Selected audio stream: {selected.stream_index if selected else 'none'}",
                    reason=selection.reason,
                    stream_index=selected.stream_index if selected else None,
                    language=selected.language if selected else None,
                    channels=selected.channels if selected else None,
                    sample_rate=selected.sample_rate if selected else source_probe.sample_rate,
                    duration=source_probe.duration,
                    input_type=input_type,
                )

            work_root = Path(tempfile.mkdtemp(prefix="subtitle-ai-sep-"))
            working = work_root / "source.wav"
            try:
                working, prepare_reason = await self._prepare_working_audio(
                    media,
                    working,
                    stream_index=selected.stream_index if selected else None,
                    start=start,
                    duration=duration,
                    debug=trace,
                    is_cancelled=is_cancelled,
                )
                prepared = await probe_audio(working, debug=trace, is_cancelled=is_cancelled)
                if not prepared.has_audio or not prepared.exists or prepared.size <= 0:
                    raise AudioSeparationError(
                        "Prepared working audio is missing a valid audio stream.",
                        code=CODE_INPUT_PREPARATION_FAILED,
                        stage="prepare",
                    )
                _emit(
                    event_cb,
                    "audio_separation_input_prepared",
                    {
                        "working_path": str(working),
                        "duration": prepared.duration,
                        "sample_rate": prepared.sample_rate,
                        "channels": prepared.channels,
                        "reason": prepare_reason,
                    },
                    debug=trace,
                )
                if trace:
                    trace.decision(prepare_reason, working_path=str(working), stage="prepare")

                _emit(
                    event_cb,
                    "audio_separation_provider_started",
                    {
                        "provider": getattr(self.provider, "name", type(self.provider).__name__),
                        "model": getattr(self.provider, "model", None),
                        "output_dir": str(dest),
                    },
                    debug=trace,
                )
                _raise_if_cancelled(is_cancelled, stage="provider")
                result = await self.provider.separate(
                    input_path=working,
                    output_dir=dest,
                    debug=trace,
                    is_cancelled=is_cancelled,
                )
                verified = await self._verify_result(
                    result,
                    input_info=prepared,
                    source_path=media,
                    source_stream=selected.stream_index if selected else None,
                    source_language=selected.language if selected else None,
                    selection_reason=selection.reason,
                    debug=trace,
                    is_cancelled=is_cancelled,
                )
                verified.debug_trace_path = trace.path if trace else None
                verified.metadata.update(
                    {
                        "task_id": run_id,
                        "job_id": job_id,
                        "input_path": str(media),
                        "input_type": input_type,
                        "selected_stream": selected.stream_index if selected else None,
                        "language": selected.language if selected else None,
                        "channels": prepared.channels,
                        "sample_rate": prepared.sample_rate,
                        "duration": prepared.duration,
                        "selection_reason": selection.reason,
                        "prepare_reason": prepare_reason,
                        "debug_trace_path": str(trace.path) if trace else None,
                    }
                )
                _emit(
                    event_cb,
                    "audio_separation_verified",
                    {
                        "dialogue": verified.dialogue.path,
                        "background": verified.background.path,
                        "input_duration": prepared.duration,
                        "dialogue_duration": verified.dialogue.duration,
                        "background_duration": verified.background.duration,
                    },
                    debug=trace,
                )
                _emit(
                    event_cb,
                    "audio_separation_completed",
                    {
                        "dialogue": verified.dialogue.path,
                        "background": verified.background.path,
                        "debug_trace_path": str(trace.path) if trace else None,
                    },
                    debug=trace,
                )
                if trace:
                    trace.finish(
                        "success",
                        dialogue=verified.dialogue.path,
                        background=verified.background.path,
                        dialogue_duration=verified.dialogue.duration,
                        background_duration=verified.background.duration,
                    )
                return verified
            finally:
                shutil.rmtree(work_root, ignore_errors=True)
        except AudioSeparationError as exc:
            self._record_failure(trace, event_cb, exc)
            raise
        except ProcessError as exc:
            wrapped = self._from_process_error(exc, stage="process")
            self._record_failure(trace, event_cb, wrapped)
            raise wrapped from exc
        except Exception as exc:
            wrapped = AudioSeparationError(
                f"Audio separation failed: {exc}",
                code=CODE_SEPARATION_FAILED,
                stage=trace.last_stage if trace else "unknown",
            )
            self._record_failure(trace, event_cb, wrapped)
            raise wrapped from exc

    async def _select_stream(
        self,
        media: Path,
        *,
        preferred_languages: list[str],
        stream_index: int | None,
        debug: DebugTrace | None,
        is_cancelled: CancelCheck | None,
    ) -> tuple[AudioSelection, str]:
        _raise_if_cancelled(is_cancelled, stage="select")
        wav_info = probe_wav(media)
        if wav_info is not None:
            return _wav_selection(media, wav_info), "audio"
        try:
            selection = await self.selector.select(
                media,
                preferred_languages=preferred_languages,
                is_cancelled=is_cancelled,
            )
        except ProcessError as exc:
            raise AudioSeparationError(
                f"Could not probe input audio streams: {exc}",
                code=CODE_INPUT_PREPARATION_FAILED,
                stage="select",
            ) from exc
        if selection.selected is None:
            raise AudioSeparationError(
                "No audio stream found in input.",
                code=CODE_INPUT_PREPARATION_FAILED,
                stage="select",
            )
        if stream_index is not None:
            match = next(
                (item for item in selection.candidates if item.stream.stream_index == stream_index),
                None,
            )
            if match is None:
                raise AudioSeparationError(
                    f"Requested audio stream {stream_index} was not found.",
                    code=CODE_INPUT_PREPARATION_FAILED,
                    stage="select",
                )
            selection = AudioSelection(
                selected=match,
                candidates=selection.candidates,
                reason=f"Explicit stream index {stream_index}",
            )
            if debug:
                debug.decision(
                    f"Selected audio stream: {stream_index}",
                    reason="explicit stream_index argument",
                )
        elif not preferred_languages and selection.selected.stream.default:
            if debug:
                debug.decision(
                    f"Selected audio stream: {selection.selected.stream.stream_index}",
                    reason="stream marked default; no explicit source language",
                )
        return selection, "media"

    async def _prepare_working_audio(
        self,
        media: Path,
        output_path: Path,
        *,
        stream_index: int | None,
        start: float | None,
        duration: float | None,
        debug: DebugTrace | None,
        is_cancelled: CancelCheck | None,
    ) -> tuple[Path, str]:
        _raise_if_cancelled(is_cancelled, stage="prepare")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        windowed = (start is not None and start > 0) or (duration is not None and duration > 0)
        if is_pcm_wav(media) and not windowed:
            shutil.copy2(media, output_path)
            reason = (
                "Input is already a WAV working format; copied without transcoding "
                "and without forcing mono or resampling"
            )
            return output_path, reason

        command = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
        if start is not None and start > 0:
            command.extend(["-ss", f"{start:.3f}"])
        command.extend(["-i", str(media)])
        if duration is not None and duration > 0:
            command.extend(["-t", f"{duration:.3f}"])
        if stream_index is not None:
            command.extend(["-map", f"0:{stream_index}"])
        command.extend(["-vn", "-c:a", "pcm_s16le", str(output_path)])
        result = await run_logged_process(
            command,
            debug=debug,
            stage="prepare",
            timeout_s=PREPARE_TIMEOUT_S,
            is_cancelled=is_cancelled,
            output_paths=[output_path],
        )
        if result.outcome is ProcessOutcome.CANCELLED:
            raise AudioSeparationError(
                "Audio separation cancelled.",
                code=CODE_CANCELLED,
                stage="prepare",
            )
        if not result.ok:
            raise AudioSeparationError(
                "ffmpeg failed to prepare working audio.",
                code=CODE_INPUT_PREPARATION_FAILED,
                stage="prepare",
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise AudioSeparationError(
                "ffmpeg produced empty working audio.",
                code=CODE_INPUT_PREPARATION_FAILED,
                stage="prepare",
            )
        reasons = ["Converted input to WAV", "provider requires a supported working format"]
        if stream_index is not None:
            reasons.append(f"mapped stream {stream_index}")
        if windowed:
            reasons.append("applied start/duration window")
        reasons.append("preserved sample rate and channels (no forced mono)")
        return output_path, "; ".join(reasons)

    async def _verify_result(
        self,
        result: SeparationResult,
        *,
        input_info: AudioFileInfo,
        source_path: Path,
        source_stream: int | None,
        source_language: str | None,
        selection_reason: str,
        debug: DebugTrace | None,
        is_cancelled: CancelCheck | None,
    ) -> SeparationResult:
        _raise_if_cancelled(is_cancelled, stage="verify")
        dialogue_path = Path(result.dialogue.path or "")
        background_path = Path(result.background.path or "")
        try:
            dialogue_info = await self._require_stem(
                dialogue_path,
                role=ROLE_DIALOGUE,
                missing_code=CODE_MISSING_DIALOGUE,
                debug=debug,
                is_cancelled=is_cancelled,
            )
            background_info = await self._require_stem(
                background_path,
                role=ROLE_BACKGROUND,
                missing_code=CODE_MISSING_BACKGROUND,
                debug=debug,
                is_cancelled=is_cancelled,
            )
            self._compare_durations(input_info, dialogue_info, background_info, debug=debug)
        except AudioSeparationError:
            _unlink(dialogue_path, debug, "Removed partial dialogue output after verification failure")
            _unlink(background_path, debug, "Removed partial background output after verification failure")
            raise

        provider = result.provider or getattr(self.provider, "name", "unknown")
        model = result.model or getattr(self.provider, "model", None)
        dialogue = self._enrich_artifact(
            result.dialogue,
            info=dialogue_info,
            role=ROLE_DIALOGUE,
            provider=provider,
            model=model,
            source_path=source_path,
            source_stream=source_stream,
            source_language=source_language,
            selection_reason=selection_reason,
        )
        background = self._enrich_artifact(
            result.background,
            info=background_info,
            role=ROLE_BACKGROUND,
            provider=provider,
            model=model,
            source_path=source_path,
            source_stream=source_stream,
            source_language=source_language,
            selection_reason=selection_reason,
        )
        metadata = dict(result.metadata)
        metadata.update(
            {
                "input_duration": input_info.duration,
                "dialogue_duration": dialogue_info.duration,
                "background_duration": background_info.duration,
                "dialogue_size": dialogue_info.size,
                "background_size": background_info.size,
                "duration_tolerance_s": (
                    duration_tolerance_s(input_info.duration) if input_info.duration else None
                ),
                "verified": True,
            }
        )
        if debug:
            debug.event(
                "verify",
                "Output stems verified",
                dialogue=str(dialogue_path),
                background=str(background_path),
                input_duration=input_info.duration,
                dialogue_duration=dialogue_info.duration,
                background_duration=background_info.duration,
                dialogue_size=dialogue_info.size,
                background_size=background_info.size,
            )
        return SeparationResult(
            dialogue=dialogue,
            background=background,
            provider=provider,
            model=model,
            metadata=metadata,
            debug_trace_path=result.debug_trace_path,
        )

    async def _require_stem(
        self,
        path: Path,
        *,
        role: str,
        missing_code: str,
        debug: DebugTrace | None,
        is_cancelled: CancelCheck | None,
    ) -> AudioFileInfo:
        if not path.is_file():
            raise AudioSeparationError(
                f"Missing {role} output: {path}",
                code=missing_code,
                stage="verify",
            )
        info = await probe_audio(path, debug=debug, is_cancelled=is_cancelled)
        if info.size <= 0:
            _unlink(path, debug, f"Removed empty {role} output")
            raise AudioSeparationError(
                f"{role.capitalize()} output is empty.",
                code=CODE_INVALID_OUTPUT,
                stage="verify",
            )
        if not info.has_audio:
            _unlink(path, debug, f"Removed invalid {role} output")
            raise AudioSeparationError(
                f"{role.capitalize()} output has no valid audio stream.",
                code=CODE_INVALID_OUTPUT,
                stage="verify",
            )
        if info.duration is None or info.duration <= 0:
            _unlink(path, debug, f"Removed {role} output with invalid duration")
            raise AudioSeparationError(
                f"{role.capitalize()} output duration is invalid.",
                code=CODE_INVALID_OUTPUT,
                stage="verify",
            )
        return info

    def _compare_durations(
        self,
        input_info: AudioFileInfo,
        dialogue: AudioFileInfo,
        background: AudioFileInfo,
        *,
        debug: DebugTrace | None,
    ) -> None:
        if input_info.duration is None:
            return
        allowed = duration_tolerance_s(input_info.duration)
        mismatches: list[str] = []
        for role, info in ((ROLE_DIALOGUE, dialogue), (ROLE_BACKGROUND, background)):
            if info.duration is None:
                mismatches.append(f"{role} duration missing")
                continue
            delta = abs(info.duration - input_info.duration)
            if delta > allowed:
                mismatches.append(
                    f"{role} duration {info.duration:.3f}s differs from input "
                    f"{input_info.duration:.3f}s by {delta:.3f}s (tolerance {allowed:.3f}s)"
                )
        if debug:
            debug.event(
                "verify",
                "Duration comparison",
                input_duration=input_info.duration,
                dialogue_duration=dialogue.duration,
                background_duration=background.duration,
                tolerance_s=allowed,
                ok=not mismatches,
            )
        if mismatches:
            _unlink(dialogue.path, debug, "Removed dialogue output after duration mismatch")
            _unlink(background.path, debug, "Removed background output after duration mismatch")
            raise AudioSeparationError(
                "Output duration verification failed: " + "; ".join(mismatches),
                code=CODE_VERIFICATION_FAILED,
                stage="verify",
            )

    def _enrich_artifact(
        self,
        artifact: AudioArtifact,
        *,
        info: AudioFileInfo,
        role: str,
        provider: str,
        model: str | None,
        source_path: Path,
        source_stream: int | None,
        source_language: str | None,
        selection_reason: str,
    ) -> AudioArtifact:
        metadata = dict(artifact.metadata)
        metadata.update(
            {
                "role": role,
                "provider": provider,
                "model": model,
                "source_path": str(source_path),
                "source_stream": source_stream,
                "selection_reason": selection_reason,
                "size": info.size,
            }
        )
        return AudioArtifact(
            type="audio",
            language=source_language or artifact.language,
            path=path_str(info.path),
            provider=provider,
            duration=info.duration,
            sample_rate=info.sample_rate or artifact.sample_rate,
            channels=info.channels or artifact.channels,
            metadata=metadata,
        )

    def _from_process_error(self, exc: ProcessError, *, stage: str) -> AudioSeparationError:
        if exc.outcome is ProcessOutcome.CANCELLED:
            return AudioSeparationError(
                "Audio separation cancelled.",
                code=CODE_CANCELLED,
                stage=stage,
            )
        return AudioSeparationError(
            str(exc),
            code=CODE_SEPARATION_FAILED,
            stage=stage,
        )

    def _record_failure(
        self,
        debug: DebugTrace | None,
        event_cb: EventCb | None,
        exc: AudioSeparationError,
    ) -> None:
        status = "cancelled" if exc.code == CODE_CANCELLED else "failed"
        event_name = "audio_separation_cancelled" if status == "cancelled" else "audio_separation_failed"
        payload = {
            "status": status,
            "stage": exc.stage,
            "code": exc.code,
            "error": str(exc),
            "last_completed_stage": debug.last_stage if debug else None,
            "debug_trace_path": str(debug.path) if debug else None,
        }
        _emit(event_cb, event_name, payload, debug=debug)
        if debug and not debug.finished:
            debug.event(
                "failure",
                f"Separation {status}",
                failure_stage=exc.stage,
                last_completed_stage=debug.last_stage,
                error=str(exc),
                code=exc.code,
            )
            if status == "cancelled":
                debug.event(
                    "cleanup",
                    "cancellation handled; partial outputs removed and no successful result returned",
                    failure_stage=exc.stage,
                )
            debug.finish(status, error=str(exc), code=exc.code, stage=exc.stage)


async def separate_audio(
    input_path: str | Path,
    **kwargs: Any,
) -> SeparationResult:
    return await AudioSeparationService().separate(input_path, **kwargs)
