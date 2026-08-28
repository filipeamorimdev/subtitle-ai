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
from app.localization.dubbing.providers.chatterbox import (
    ChatterboxTTSProvider,
    ChatterboxVoiceProfile,
    load_chatterbox_model,
    recommended_voice_models_for_language,
    resolve_voice_model_for_language,
    resolve_voice_profile,
    tts_output_ignores_text,
    write_chatterbox_wav,
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

_write_chatterbox_wav = write_chatterbox_wav


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
    "ChatterboxTTSProvider",
    "ChatterboxVoiceProfile",
    "load_chatterbox_model",
    "recommended_voice_models_for_language",
    "probe_duration_seconds",
    "probe_has_audio_stream",
    "resolve_voice_model_for_language",
    "resolve_voice_profile",
    "run_process_checked",
    "tts_output_ignores_text",
    "write_tts_timeline_wav",
    "_write_chatterbox_wav",
]

_ = (Any, Awaitable, Callable, JobEventLog, Path, ProcessError)
