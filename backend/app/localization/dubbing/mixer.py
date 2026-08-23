"""Final dialogue-track loudness. Peak-normalize + limiter, optional ffmpeg loudnorm."""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.media.process_runner import ProcessError, run_process_checked

logger = get_logger("dubbing.mixer")


async def finalize_dialogue_track(
    wav_path: str | Path,
    *,
    use_loudnorm: bool = True,
    is_cancelled=None,
) -> Path:
    """Apply optional loudnorm. Peak limiting already happened on the timeline."""
    path = Path(wav_path)
    if not use_loudnorm:
        return path
    output = path.with_name(path.stem + ".loudnorm.wav")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        str(output),
    ]
    try:
        await run_process_checked(
            command,
            timeout_s=600.0,
            is_cancelled=is_cancelled,
            output_paths=[output],
        )
    except ProcessError as exc:
        logger.warning("loudnorm skipped: %s", exc)
        return path
    if output.is_file() and output.stat().st_size > 0:
        output.replace(path)
        logger.info("dialogue_loudnorm path=%s", path)
    return path
