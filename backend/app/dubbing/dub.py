"""Text-to-speech dubbing preview from an existing target SRT.

v1 behavior (sidecar MKV):
- Input: a ready-to-use *target-language* `.srt`
- Output: `{stem}.{lang}.dub.mkv` next to the original media
- The original file is never overwritten.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import re
import shutil
import tempfile
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.jobs.event_log import JobEventLog
from app.subtitles.filenames import sidecar_language_tag
from app.subtitles.reading import parse_srt_timestamp
from app.subtitles.parsers.srt import parse_srt

logger = get_logger("dubbing")

# Piper clips are quiet. Boost each cue and the assembled timeline so the TTS
# track is audible beside the original.
TTS_CUE_GAIN_DB = 6.0
TTS_MIX_GAIN_DB = 18.0
TTS_LIMITER_CEILING = 0.98

TAG_RE = re.compile(r"</?(?:i|b|u)>", flags=re.IGNORECASE)
MUSIC_RE = re.compile(r"[♪♫🎵🎶]+")
SFX_ONLY_RE = re.compile(r"^(?:\([^)]*\)\s*)+$")
SPEAKER_PREFIX_RE = re.compile(r"^(?:[-–—]\s*)*(?P<name>[^:]{1,40}):\s+")
LEADING_DASH_RE = re.compile(r"(?:^|\s)[-–—]\s*")
PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_PATTERN = re.compile(
    r"^(?P<lang_family>[^-]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$"
)
# Shaped cue clips are 16 kHz mono. The speech track is assembled in PCM at this
# rate (not via ffmpeg adelay/amix, which loops a buffer on long timelines).
CUE_SAMPLE_RATE = 16000
MAX_TTS_TIMELINE_HOURS = 6.0


class DubError(Exception):
    pass


def _piper_voice_download_urls(voice_model: str) -> tuple[str, str, str]:
    """Build HuggingFace URLs for a Piper voice (.onnx + .onnx.json)."""
    match = VOICE_PATTERN.match(voice_model.strip())
    if not match:
        raise DubError(
            f"Invalid Piper voice model: {voice_model} "
            "(expected format like en_US-lessac-medium)"
        )

    lang_family = match.group("lang_family")
    lang_code = f"{lang_family}_{match.group('lang_region')}"
    voice_name = match.group("voice_name")
    voice_quality = match.group("voice_quality")
    voice_code = f"{lang_code}-{voice_name}-{voice_quality}"

    def build_url(extension: str) -> str:
        filename = f"{voice_code}{extension}"
        segments = [lang_family, lang_code, voice_name, voice_quality, filename]
        path = "/".join(quote(segment, safe="") for segment in segments)
        return f"{PIPER_VOICES_BASE}/{path}?download=true"

    return voice_code, build_url(".onnx"), build_url(".onnx.json")


async def _download_piper_file(
    url: str,
    dest: Path,
    *,
    is_cancelled: Callable[[], bool],
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".partial")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(1800.0)) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with temp.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if is_cancelled():
                            raise DubError("Dub cancelled")
                        handle.write(chunk)
        temp.replace(dest)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


async def ensure_piper_voice_available(
    *,
    voice_model: str,
    voices_dir: Path,
    is_cancelled: Callable[[], bool],
) -> Path:
    """Ensure Piper voice files exist under `voices_dir`.

    We download directly from HuggingFace with URL-encoded path segments.
    Piper's bundled `download_voices` helper uses stdlib `urlopen`, which
    fails on non-ASCII voice names such as ``pt_PT-tugão-medium``.

    Returns the path to the ``.onnx`` model.
    """
    voices_dir.mkdir(parents=True, exist_ok=True)
    voice_code, model_url, config_url = _piper_voice_download_urls(voice_model)
    model_path = voices_dir / f"{voice_code}.onnx"
    config_path = voices_dir / f"{voice_code}.onnx.json"

    if not (model_path.is_file() and model_path.stat().st_size > 0 and config_path.is_file()):
        logger.info("Downloading Piper voice model=%s into %s", voice_model, voices_dir)
        if not model_path.is_file() or model_path.stat().st_size <= 0:
            await _download_piper_file(model_url, model_path, is_cancelled=is_cancelled)
        if not config_path.is_file() or config_path.stat().st_size <= 0:
            await _download_piper_file(config_url, config_path, is_cancelled=is_cancelled)

    return model_path


def clean_text_for_tts(text: str) -> str:
    """Normalize cue text for Piper. Empty result means skip the cue."""
    stripped = TAG_RE.sub("", text or "")
    stripped = MUSIC_RE.sub(" ", stripped)
    stripped = stripped.replace("\n", " ")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    stripped = LEADING_DASH_RE.sub(" ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    speaker = SPEAKER_PREFIX_RE.match(stripped)
    if speaker:
        stripped = stripped[speaker.end() :].strip()
    if not stripped or SFX_ONLY_RE.match(stripped):
        return ""
    return stripped


async def probe_has_audio_stream(path: str | Path) -> bool:
    """Return True when `path` contains at least one audio stream."""
    p = Path(path)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
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
    except (TimeoutError, FileNotFoundError):
        return False

    if proc.returncode != 0:
        return False

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return False

    return bool(payload.get("streams"))


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
            str(CUE_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-af",
            f"volume={TTS_CUE_GAIN_DB}dB,apad,atrim=0:{target_duration_s}",
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
        str(CUE_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-af",
        # atempo -> apad -> trim. apad ensures we can always pad up to target_duration_s.
        f"atempo={atempo},volume={TTS_CUE_GAIN_DB}dB,apad,atrim=0:{target_duration_s}",
        str(output_wav),
    ]
    await run_process_checked(cmd, timeout_s=1800.0)
    return (fit, atempo, audio_duration)


def load_piper_voice(model_path: Path) -> Any:
    """Load a Piper ONNX voice once per dub job."""
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise DubError("piper-tts is not installed") from exc
    try:
        return PiperVoice.load(str(model_path))
    except Exception as exc:  # noqa: BLE001
        raise DubError(f"Failed to load Piper voice {model_path.name}: {exc}") from exc


def _write_piper_wav(voice: Any, text: str, output_wav: Path) -> None:
    """Write one utterance using the piper-tts Python API."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as wav_file:
        synthesize_wav = getattr(voice, "synthesize_wav", None)
        if callable(synthesize_wav):
            synthesize_wav(text, wav_file)
            return
        try:
            voice.synthesize(text, wav_file)
            return
        except TypeError:
            pass
        params_set = False
        for chunk in voice.synthesize(text):
            if not params_set:
                wav_file.setframerate(getattr(chunk, "sample_rate", CUE_SAMPLE_RATE))
                wav_file.setsampwidth(getattr(chunk, "sample_width", 2))
                wav_file.setnchannels(getattr(chunk, "sample_channels", 1))
                params_set = True
            audio = getattr(chunk, "audio_int16_bytes", None)
            if audio:
                wav_file.writeframes(audio)


def piper_output_ignores_text(samples: list[tuple[int, float]]) -> bool:
    """True when clip length does not follow text length (stdin ignored / same junk)."""
    if len(samples) < 8:
        return False
    chars = [count for count, _duration in samples]
    durations = [duration for _count, duration in samples]
    if max(chars) - min(chars) < 20:
        return False
    return max(durations) - min(durations) < 0.25


async def synthesize_piper_to_wav(
    text: str,
    *,
    voice: Any,
    output_wav: Path,
    is_cancelled: Callable[[], bool],
) -> None:
    """Synthesize one cue with a loaded Piper voice (Python API, not the CLI).

    Piper 1.3+ treats unknown CLI flags as the text to speak. Passing
    ``--download-dir`` made every cue synthesize the same ~1.5s of junk.
    """
    if not output_wav.parent.exists():
        output_wav.parent.mkdir(parents=True, exist_ok=True)
    if is_cancelled():
        raise DubError("Dub cancelled")

    await asyncio.to_thread(_write_piper_wav, voice, text, output_wav)

    if is_cancelled():
        raise DubError("Dub cancelled")
    if not output_wav.is_file() or output_wav.stat().st_size < 64:
        raise DubError("piper produced no audio for cue")


def _start_sample(start_ms: int, sample_rate: int) -> int:
    return max(0, int(round(max(0, start_ms) * sample_rate / 1000.0)))


def _read_pcm_s16_mono(path: Path) -> tuple[int, array.array]:
    """Read a WAV as mono signed 16-bit samples."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise DubError(f"Expected 16-bit PCM in {path.name}, got {width * 8}-bit")
    if rate <= 0:
        raise DubError(f"Invalid sample rate in {path.name}")
    samples = array.array("h")
    samples.frombytes(frames)
    if channels <= 0:
        raise DubError(f"No audio channels in {path.name}")
    if channels > 1:
        samples = array.array("h", (samples[index] for index in range(0, len(samples), channels)))
    return rate, samples


def write_tts_timeline_wav(
    shaped_clips: list[tuple[Path, int]],
    output_wav: Path,
    *,
    media_duration_s: float | None = None,
    sample_rate: int = CUE_SAMPLE_RATE,
) -> None:
    """Place short cue clips onto a silent PCM timeline and write one WAV.

    ffmpeg adelay/amix is intentionally not used here: delaying hundreds of clips
    internally prepends minutes of silence, and mixing those long streams loops a
    buffer so the same sound repeats instead of the subtitle text.
    """
    if not shaped_clips:
        raise DubError("No cue clips to mix")

    gain = 10 ** (TTS_MIX_GAIN_DB / 20.0)
    peak_limit = int(32767 * TTS_LIMITER_CEILING)
    placed: list[tuple[int, array.array]] = []
    last_sample = 0

    for clip_path, start_ms in shaped_clips:
        clip_rate, samples = _read_pcm_s16_mono(clip_path)
        if clip_rate != sample_rate:
            raise DubError(
                f"Cue clip {clip_path.name} is {clip_rate} Hz, expected {sample_rate} Hz"
            )
        start = _start_sample(start_ms, sample_rate)
        last_sample = max(last_sample, start + len(samples))
        placed.append((start, samples))

    total_samples = last_sample
    if media_duration_s is not None and media_duration_s > 0:
        total_samples = max(total_samples, int(round(media_duration_s * sample_rate)))
    if total_samples <= 0:
        raise DubError("TTS timeline would be empty")

    max_samples = int(MAX_TTS_TIMELINE_HOURS * 3600 * sample_rate)
    if total_samples > max_samples:
        raise DubError(
            f"TTS timeline is too long ({total_samples / sample_rate:.1f}s, "
            f"max {MAX_TTS_TIMELINE_HOURS:.0f}h)"
        )

    timeline = array.array("h", bytes(total_samples * 2))
    for start, samples in placed:
        for offset, value in enumerate(samples):
            index = start + offset
            if index >= total_samples:
                break
            mixed = int(timeline[index] + value * gain)
            if mixed > peak_limit:
                mixed = peak_limit
            elif mixed < -peak_limit:
                mixed = -peak_limit
            timeline[index] = mixed

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(timeline.tobytes())


def build_mux_command(
    media: Path,
    tts_audio_wav: Path,
    output: Path,
    *,
    lang_tag: str,
    copy_original_audio: bool,
) -> list[str]:
    """Build ffmpeg mux command, copying original audio when present."""
    cmd = [
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
        "-c:v",
        "copy",
    ]
    if copy_original_audio:
        cmd.extend(
            [
                "-map",
                "0:a:0",
                "-map",
                "1:a:0",
                "-c:a:0",
                "copy",
                "-c:a:1",
                "aac",
                "-b:a:1",
                "192k",
                "-metadata:s:a:1",
                f"language={lang_tag}",
                "-metadata:s:a:1",
                "title=Dub (TTS)",
            ]
        )
    else:
        cmd.extend(
            [
                "-map",
                "1:a:0",
                "-c:a:0",
                "aac",
                "-b:a:0",
                "192k",
                "-metadata:s:a:0",
                f"language={lang_tag}",
                "-metadata:s:a:0",
                "title=Dub (TTS)",
            ]
        )
    cmd.append(str(output))
    return cmd


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
    model_path = await ensure_piper_voice_available(
        voice_model=voice_model,
        voices_dir=voices_dir,
        is_cancelled=is_cancelled,
    )
    if is_cancelled():
        event_log.record(event="cancelled")
        return
    voice = await asyncio.to_thread(load_piper_voice, model_path)
    event_log.record(event="voice_loaded", voice_model=voice_model, path=str(model_path))

    with tempfile.TemporaryDirectory(prefix="subtitle-ai-dub-") as tmp:
        tmp_dir = Path(tmp)
        shaped_clips: list[tuple[Path, int]] = []

        speed_cap = 1.2
        piper_input_count = 0
        synth_samples: list[tuple[int, float]] = []

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
            cue_wav = tmp_dir / f"cue-{block_idx}.wav"
            shaped_wav = tmp_dir / f"cue-{block_idx}-shaped.wav"

            event_log.record(
                event="cue",
                index=block.index,
                start_ms=start_ms,
                end_ms=end_ms,
                chars=chars,
                fit="synth",
                text_preview=text[:80],
            )

            await synthesize_piper_to_wav(
                text,
                voice=voice,
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

            if audio_duration is not None:
                synth_samples.append((chars, audio_duration))

            shaped_clips.append((shaped_wav, start_ms))

            if on_progress:
                maybe = on_progress(block_idx, total)
                if maybe is not None:
                    await maybe

        # Place all shaped clips onto one speech-only timeline, then mux.
        if not shaped_clips:
            raise DubError("No cue text was synthesized; dub output would be silence.")
        if piper_output_ignores_text(synth_samples):
            raise DubError(
                "Piper produced nearly the same clip length for every subtitle. "
                "The voice is not reading cue text; refusing to mux a looping track."
            )

        media_duration_s = await probe_duration_seconds(media)
        if media_duration_s is None:
            last_end_ms = max(
                (parse_srt_timestamp(block.end) or 0 for block in doc.blocks),
                default=0,
            )
            if last_end_ms > 0:
                media_duration_s = last_end_ms / 1000.0

        event_log.record(
            event="mix",
            input_clips=len(shaped_clips),
            voice_model=voice_model,
            media_duration_s=media_duration_s,
        )

        tts_audio_wav = tmp_dir / "tts-audio.wav"
        if is_cancelled():
            event_log.record(event="cancelled")
            return

        await asyncio.to_thread(
            write_tts_timeline_wav,
            shaped_clips,
            tts_audio_wav,
            media_duration_s=media_duration_s,
        )

        event_log.record(event="audio_ready", path=str(tts_audio_wav))

        # Mux into a new mkv next to the original.
        event_log.record(event="mux_start", output_path=str(out))

        tmp_out = tmp_dir / out.name
        lang_tag = sidecar_language_tag(target_language)

        copy_original_audio = await probe_has_audio_stream(media)
        mux_cmd = build_mux_command(
            media,
            tts_audio_wav,
            tmp_out,
            lang_tag=lang_tag,
            copy_original_audio=copy_original_audio,
        )

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

