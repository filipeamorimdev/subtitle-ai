"""Text-to-speech dubbing preview from an existing target SRT.

Compatibility facade. The pipeline lives in ``app.localization.dubbing``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from app.jobs.event_log import JobEventLog
from app.localization.dubbing.dialogue import clean_text_for_tts
from app.localization.dubbing.pipeline import (
    DubError,
    build_mux_command,
    dub_media_from_srt_to_mkv,
    probe_duration_seconds,
    probe_has_audio_stream,
)
from app.localization.dubbing.providers.piper import (
    ensure_piper_voice_available,
    load_piper_voice,
    piper_output_ignores_text,
    piper_voice_download_urls,
    resolve_voice_model_for_language,
    write_piper_wav,
)
from app.localization.dubbing.timeline import (
    CUE_SAMPLE_RATE,
    write_tts_timeline_wav,
)
from app.media.process_runner import ProcessError, run_process_checked as _run_process_checked

# Historic name kept so older tests/docs still import it. Gain is no longer
# applied as a hardcoded mix boost; the timeline peak-normalizes instead.
TTS_CUE_GAIN_DB = 0.0
TTS_MIX_GAIN_DB = 0.0
TTS_LIMITER_CEILING = 0.99

_piper_voice_download_urls = piper_voice_download_urls
_write_piper_wav = write_piper_wav


async def run_process_checked(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[bytes, bytes]:
    result = await _run_process_checked(
        argv,
        input_text=input_text,
        timeout_s=timeout_s,
        is_cancelled=is_cancelled,
    )
    return result.stdout, result.stderr


__all__ = [
    "CUE_SAMPLE_RATE",
    "DubError",
    "TTS_CUE_GAIN_DB",
    "TTS_LIMITER_CEILING",
    "TTS_MIX_GAIN_DB",
    "build_mux_command",
    "clean_text_for_tts",
    "dub_media_from_srt_to_mkv",
    "ensure_piper_voice_available",
    "load_piper_voice",
    "piper_output_ignores_text",
    "probe_duration_seconds",
    "probe_has_audio_stream",
    "resolve_voice_model_for_language",
    "run_process_checked",
    "write_tts_timeline_wav",
    "_piper_voice_download_urls",
    "_write_piper_wav",
]

_ = (Any, Awaitable, Callable, JobEventLog, Path, ProcessError)
