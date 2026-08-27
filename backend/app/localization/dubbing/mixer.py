"""Final dialogue-track loudness. Peak-normalize + limiter, optional ffmpeg loudnorm."""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.media.process_runner import ProcessError, ProcessOutcome, run_process_checked

logger = get_logger("dubbing.mixer")

DUB_OUTPUT_SAMPLE_RATE = 48_000


def build_background_mix_command(
    background_wav: str | Path,
    dialogue_wav: str | Path,
    output_wav: str | Path,
    *,
    use_loudnorm: bool = True,
) -> list[str]:
    """Build a deterministic background + translated-dialogue mix at 48 kHz stereo.

    The translated dialogue sidechains the bed slightly while it is active, then
    both signals are combined.  This keeps music, ambience, and effects audible
    without burying the dub under the original-language vocal stem.
    """
    filters = [
        "[0:a]aresample=48000,aformat=channel_layouts=stereo[background]",
        "[1:a]aresample=48000,aformat=channel_layouts=mono,pan=stereo|c0=c0|c1=c0[dialogue]",
        "[background][dialogue]sidechaincompress=threshold=0.035:ratio=5:attack=20:release=350[ducked]",
        "[ducked][dialogue]amix=inputs=2:duration=longest:normalize=0[mixed]",
    ]
    output_label = "mixed"
    if use_loudnorm:
        filters.append("[mixed]loudnorm=I=-18:TP=-1.5:LRA=11[final]")
        output_label = "final"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(background_wav),
        "-i",
        str(dialogue_wav),
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{output_label}]",
        "-ac",
        "2",
        "-ar",
        str(DUB_OUTPUT_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(output_wav),
    ]


async def mix_background_and_dialogue_track(
    background_wav: str | Path,
    dialogue_wav: str | Path,
    output_wav: str | Path,
    *,
    use_loudnorm: bool = True,
    is_cancelled=None,
) -> Path:
    """Create the final background-preserving dub WAV or raise on failure."""
    output = Path(output_wav)
    command = build_background_mix_command(
        background_wav,
        dialogue_wav,
        output,
        use_loudnorm=use_loudnorm,
    )
    try:
        await run_process_checked(
            command,
            timeout_s=1800.0,
            is_cancelled=is_cancelled,
            output_paths=[output],
        )
    except ProcessError as exc:
        if exc.outcome is ProcessOutcome.CANCELLED:
            raise
        raise RuntimeError(f"Could not mix background and dialogue: {exc}") from exc
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("Background mix produced no audio.")
    return output


async def finalize_dialogue_track(
    wav_path: str | Path,
    *,
    use_loudnorm: bool = True,
    output_sample_rate: int = DUB_OUTPUT_SAMPLE_RATE,
    is_cancelled=None,
) -> Path:
    """Normalize optionally, but always encode the dialogue timeline at the target rate."""
    path = Path(wav_path)
    output = path.with_name(path.stem + ".loudnorm.wav")
    audio_filter = "loudnorm=I=-16:TP=-1.5:LRA=11" if use_loudnorm else "anull"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-af",
        audio_filter,
        "-ac",
        "1",
        "-ar",
        str(output_sample_rate),
        "-c:a",
        "pcm_s16le",
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
        if exc.outcome is ProcessOutcome.CANCELLED:
            raise
        logger.warning("loudnorm skipped: %s", exc)
        return path
    if output.is_file() and output.stat().st_size > 0:
        output.replace(path)
        logger.info("dialogue_audio_finalized path=%s loudnorm=%s", path, use_loudnorm)
    return path
