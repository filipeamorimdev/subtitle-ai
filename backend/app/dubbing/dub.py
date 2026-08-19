"""Text-to-speech dubbing preview from an existing target SRT.

v1 behavior (sidecar MKV):
- Input: a ready-to-use *target-language* `.srt`
- Output: `{stem}.{lang}.dub.mkv` next to the original media
- The original file is never overwritten.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
from app.jobs.event_log import JobEventLog
from app.subtitles.filenames import sidecar_language_tag
from app.subtitles.reading import parse_srt_timestamp
from app.subtitles.parsers.srt import parse_srt

logger = get_logger("dubbing")

TAG_RE = re.compile(r"</?(?:i|b|u)>", flags=re.IGNORECASE)


class DubError(Exception):
    pass


def clean_text_for_tts(text: str) -> str:
    # Strip the subset of player-friendly tags we keep elsewhere in the pipeline.
    stripped = TAG_RE.sub("", text or "")
    return stripped.replace("\n", " ").strip()


async def probe_duration_seconds(path: str | Path) -> float | None:
    """Return ffprobe duration in seconds, or None if unreadable."""
    p = Path(path)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(p),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except TimeoutError as exc:
        raise DubError(f"ffprobe timed out for {p.name}") from exc
    except FileNotFoundError as exc:
        raise DubError("ffprobe is not installed") from exc

    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[:300]
        raise DubError(f"ffprobe failed for {p.name}: {detail or 'unknown error'}")

    try:
        payload = json.loads(stdout.decode("utf-8"))
        duration = float((payload.get("format") or {}).get("duration"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


async def run_process_checked(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[bytes, bytes]:
    """Run a subprocess and raise on non-zero exit / timeout.

    Designed for long-running external calls (piper / ffmpeg) that should be
    stoppable when the DB job is cancelled.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        communicate_kwargs: dict[str, Any] = {}
        if input_text is not None:
            communicate_kwargs["input"] = input_text.encode("utf-8")

        if timeout_s is not None:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(**communicate_kwargs),
                timeout=timeout_s,
            )
        else:
            stdout, stderr = await proc.communicate(**communicate_kwargs)
        if is_cancelled and is_cancelled():
            proc.kill()
        if proc.returncode != 0:
            raise DubError(
                f"Command failed exit={proc.returncode} stderr={(stderr or b'')[-400:].decode('utf-8', errors='replace')}"
            )
        return stdout or b"", stderr or b""
    except asyncio.CancelledError:
        try:
            proc.kill()
        finally:
            raise
    except TimeoutError as exc:
        try:
            proc.kill()
        finally:
            raise DubError(f"Command timed out: {argv[:2]}...") from exc


async def shape_clip_to_duration(
    input_wav: Path,
    output_wav: Path,
    *,
    target_duration_s: float,
    speed_cap: float,
) -> tuple[str, float, float | None]:
    """Produce an audio clip that fits within `target_duration_s`."""
    audio_duration = await probe_duration_seconds(input_wav)
    if audio_duration is None:
        # When ffprobe can't read duration, just trim/pad to the target.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(input_wav),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-af",
            f"apad,atrim=0:{target_duration_s}",
            str(output_wav),
        ]
        await run_process_checked(cmd, timeout_s=1800.0)
        return ("pad", 1.0, audio_duration)

    atempo = 1.0
    fit: str
    if audio_duration < target_duration_s:
        fit = "pad"
    elif audio_duration > target_duration_s:
        # To reduce duration we speed up by a factor of (audio/target).
        ratio = audio_duration / max(0.001, target_duration_s)
        atempo = min(ratio, speed_cap)
        fit = "atempo" if atempo > 1.001 else "overrun"
    else:
        fit = "exact"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_wav),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-af",
        # atempo -> apad -> trim. apad ensures we can always pad up to target_duration_s.
        f"atempo={atempo},apad,atrim=0:{target_duration_s}",
        str(output_wav),
    ]
    await run_process_checked(cmd, timeout_s=1800.0)
    return (fit, atempo, audio_duration)


async def synthesize_piper_to_wav(
    text: str,
    *,
    voice_model: str,
    voices_dir: Path,
    output_wav: Path,
    is_cancelled: Callable[[], bool],
) -> None:
    """Use `piper` CLI from piper-tts to synthesize wav for one cue."""
    if not output_wav.parent.exists():
        output_wav.parent.mkdir(parents=True, exist_ok=True)
    piper_bin = shutil.which("piper")
    if not piper_bin:
        raise DubError("piper binary is not installed (expected piper-tts package)")

    # Note: `piper` downloads the voice model on first use.
    # We try to keep downloads under `/config/piper-voices` by passing both dirs.
    cmd = [
        "piper",
        "--model",
        voice_model,
        "--output_file",
        str(output_wav),
        "--data-dir",
        str(voices_dir),
        "--download-dir",
        str(voices_dir),
    ]

    # Pass cue text via stdin. Piper supports arbitrary unicode input.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode("utf-8")),
            timeout=1800.0,
        )
    except asyncio.CancelledError:
        proc.kill()
        raise

    if is_cancelled():
        proc.kill()
        raise DubError("Dub cancelled")

    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise DubError(f"piper failed for model={voice_model}: {detail or 'unknown error'}")
    _ = stdout


def resolve_voice_model_for_language(target_language: str) -> str:
    lang = (target_language or "").strip()
    normalized = lang.replace("_", "-")
    if normalized.lower() == "pt-pt":
        # Portuguese (Portugal): tugão (medium)
        return "pt_PT-tugão-medium"
    if normalized.lower() == "pt-br":
        return "pt_BR-faber-medium"
    # Fallback: English.
    return "en_US-lessac-medium"


async def dub_media_from_srt_to_mkv(
    *,
    media_path: str | Path,
    source_srt_path: str | Path,
    target_language: str,
    output_path: str | Path,
    voice_model: str | None = None,
    event_log: JobEventLog,
    is_cancelled: Callable[[], bool],
    on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
) -> None:
    """Generate preview `.dub.mkv` from `source_srt_path`."""
    media = Path(media_path)
    source_srt = Path(source_srt_path)
    out = Path(output_path)
    voices_dir = Path("/config/piper-voices")

    if not media.is_file():
        raise DubError(f"Media file is not readable: {media}")
    if not source_srt.is_file():
        raise DubError(f"Source SRT is missing: {source_srt}")
    if out.exists() and out.stat().st_size > 0:
        raise DubError(f"Output dub already exists: {out.name}")

    event_log.record(event="started", media_path=str(media), target_language=target_language)

    doc = None
    try:
        content = source_srt.read_text(encoding="utf-8")
        doc = parse_srt(content, encoding="utf-8")
    except Exception as exc:
        raise DubError(f"Failed to parse SRT {source_srt.name}: {exc}") from exc

    total = len(doc.blocks)
    event_log.record(event="source_srt", path=str(source_srt), cue_count=total)

    voice_model = voice_model or resolve_voice_model_for_language(target_language)

    with tempfile.TemporaryDirectory(prefix="subtitle-ai-dub-") as tmp:
        tmp_dir = Path(tmp)
        shaped_clips: list[tuple[Path, int]] = []

        speed_cap = 1.2
        piper_input_count = 0

        for block_idx, block in enumerate(doc.blocks, start=1):
            if is_cancelled():
                event_log.record(event="cancelled")
                return

            start_ms = parse_srt_timestamp(block.start) or 0
            end_ms = parse_srt_timestamp(block.end) or 0
            target_ms = max(0, end_ms - start_ms)
            target_s = target_ms / 1000.0

            text = clean_text_for_tts(block.text)
            chars = len(text)

            if target_s <= 0.001 or not text:
                # Silence / empty cue: keep timing by skipping audio synthesis,
                # but still record the timeline step.
                event_log.record(
                    event="cue",
                    index=block.index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    chars=chars,
                    fit="skip",
                )
                if on_progress:
                    maybe = on_progress(block_idx, total)
                    if maybe is not None:
                        await maybe
                continue

            piper_input_count += 1
            cue_wav = tmp_dir / f"cue-{block.index}.wav"
            shaped_wav = tmp_dir / f"cue-{block.index}-shaped.wav"

            event_log.record(
                event="cue",
                index=block.index,
                start_ms=start_ms,
                end_ms=end_ms,
                chars=chars,
                fit="synth",
            )

            await synthesize_piper_to_wav(
                text,
                voice_model=voice_model,
                voices_dir=voices_dir,
                output_wav=cue_wav,
                is_cancelled=is_cancelled,
            )

            if is_cancelled():
                event_log.record(event="cancelled")
                return

            fit, atempo, audio_duration = await shape_clip_to_duration(
                cue_wav,
                shaped_wav,
                target_duration_s=target_s,
                speed_cap=speed_cap,
            )

            event_log.record(
                event="cue",
                index=block.index,
                start_ms=start_ms,
                end_ms=end_ms,
                chars=chars,
                fit=fit,
                atempo=atempo,
                audio_duration_s=audio_duration,
                target_duration_s=target_s,
            )

            shaped_clips.append((shaped_wav, start_ms))

            if on_progress:
                maybe = on_progress(block_idx, total)
                if maybe is not None:
                    await maybe

        # Mix all shaped clips into one timeline audio track.
        if not shaped_clips:
            raise DubError("No cue text was synthesized; dub output would be silence.")

        event_log.record(event="mix", input_clips=len(shaped_clips), voice_model=voice_model)

        # Build filter graph: adelay + amix
        inputs = []
        delay_filters = []
        for i, (clip_path, start_ms) in enumerate(shaped_clips):
            inputs.append(["-i", str(clip_path)])
            delay_filters.append(
                f"[{i}:a]adelay={start_ms}|{start_ms}[d{i}]"
            )

        # Note: We do not force a fixed output duration; `amix` uses the longest input.
        filter_graph = ";".join(delay_filters) + ";"
        labels = "".join([f"[d{i}]" for i in range(len(shaped_clips))])
        filter_graph += f"{labels}amix=inputs={len(shaped_clips)}:duration=longest:dropout_transition=0[out]"

        tts_audio_wav = tmp_dir / "tts-audio.wav"
        mix_cmd = (
            ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
            + sum(inputs, [])
            + [
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(tts_audio_wav),
            ]
        )

        if is_cancelled():
            event_log.record(event="cancelled")
            return

        # Use a generous timeout: large SRTs can take a while.
        await run_process_checked(mix_cmd, timeout_s=12 * 3600.0, is_cancelled=is_cancelled)

        event_log.record(event="audio_ready", path=str(tts_audio_wav))

        # Mux into a new mkv next to the original.
        event_log.record(event="mux_start", output_path=str(out))

        tmp_out = tmp_dir / out.name
        lang_tag = sidecar_language_tag(target_language)

        mux_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(media),
            "-i",
            str(tts_audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata:s:a:1",
            f"language={lang_tag}",
            "-metadata:s:a:1",
            "title=Dub (TTS)",
            str(tmp_out),
        ]

        await run_process_checked(mux_cmd, timeout_s=12 * 3600.0, is_cancelled=is_cancelled)

        if is_cancelled():
            event_log.record(event="cancelled")
            return

        if not tmp_out.is_file() or tmp_out.stat().st_size <= 0:
            raise DubError("Mux step produced no output.")

        # Atomic-ish move into media directory (avoid ffmpeg path handling bugs).
        staging = out.with_name(f".{out.name}.tmp")
        try:
            shutil.copyfile(tmp_out, staging)
            staging.replace(out)
        finally:
            if staging.exists():
                try:
                    staging.unlink()
                except OSError:
                    pass

        event_log.record(event="completed", output_path=str(out), piper_inputs=piper_input_count)

