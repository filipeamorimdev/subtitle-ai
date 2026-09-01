"""Probe and extract embedded subtitle tracks via ffprobe/ffmpeg."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.logging import get_logger
from app.media.process_runner import ProcessError, ProcessOutcome, run_process_checked
from app.subtitles.filenames import (
    is_origin_language,
    origin_language_rank,
    normalize_language_code,
)
from app.subtitles.ocr import OcrError, ocr_available, pgs_sup_to_srt

logger = get_logger("embedded")

TEXT_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "mov_text",
    "text",
    "webvtt",
    "wvtt",
    "ttml",
    "timed_text",
    "tx3g",
}

PGS_CODECS = {
    "hdmv_pgs_subtitle",
    "pgssub",
}

IMAGE_CODECS = {
    *PGS_CODECS,
    "dvd_subtitle",
    "dvdsub",
    "dvb_subtitle",
    "xsub",
    "vobsub",
}

TEXT_EXTRACT_TIMEOUT = 300.0
PGS_EXTRACT_TIMEOUT = 1800.0
CancelCheck = Callable[[], bool]


class EmbeddedError(Exception):
    pass


@dataclass(frozen=True)
class EmbeddedTrack:
    stream_index: int | None
    language: str | None
    codec: str | None
    kind: str  # text | image | unknown
    extractable: bool
    hi: bool = False
    forced: bool = False
    title: str | None = None
    source: str = "ffprobe"

    @property
    def label(self) -> str:
        lang = self.language or "und"
        if self.kind == "text":
            suffix = "text"
        elif self.kind == "image":
            suffix = "image"
        else:
            suffix = "embedded"
        extras: list[str] = []
        if self.hi:
            extras.append("HI")
        if self.forced:
            extras.append("forced")
        extra = f" · {'/'.join(extras)}" if extras else ""
        return f"{lang} ({suffix}{extra})"


def ffmpeg_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def classify_codec(codec: str | None) -> tuple[str, bool]:
    if not codec:
        return "unknown", False
    key = codec.strip().lower()
    if key in TEXT_CODECS:
        return "text", True
    if key in PGS_CODECS:
        return "image", ocr_available()
    if key in IMAGE_CODECS:
        return "image", False
    # Unknown subtitle codecs: do not attempt extraction
    return "unknown", False


def _parse_bool_tag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


async def probe_subtitle_tracks(
    media_path: str | Path,
    *,
    timeout: float = 12.0,
    is_cancelled: CancelCheck | None = None,
) -> list[EmbeddedTrack]:
    path = Path(media_path)
    if not path.is_file():
        return []
    if not shutil.which("ffprobe"):
        logger.warning("ffprobe not found; cannot probe embedded subtitles")
        return []

    command = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "s",
        str(path),
    ]
    try:
        result = await run_process_checked(
            command,
            timeout_s=timeout,
            is_cancelled=is_cancelled,
        )
    except ProcessError as exc:
        if exc.outcome is ProcessOutcome.CANCELLED:
            raise asyncio.CancelledError from exc
        if exc.outcome is ProcessOutcome.TIMEOUT:
            raise EmbeddedError(f"ffprobe timed out for {path.name}") from exc
        raise EmbeddedError(f"ffprobe failed for {path.name}: {exc.stderr or 'unknown error'}") from exc

    stdout, stderr = result.stdout, result.stderr
    if result.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[:300]
        raise EmbeddedError(f"ffprobe failed for {path.name}: {detail or 'unknown error'}")

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise EmbeddedError(f"ffprobe returned invalid JSON for {path.name}") from exc

    tracks: list[EmbeddedTrack] = []
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        codec = stream.get("codec_name")
        kind, extractable = classify_codec(str(codec) if codec else None)
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
        lang = normalize_language_code(tags.get("language") or tags.get("LANGUAGE"))
        title = tags.get("title")
        hi = _parse_bool_tag(disposition.get("hearing_impaired")) or (
            "hi" in str(title or "").lower() or "sdh" in str(title or "").lower()
        )
        forced = _parse_bool_tag(disposition.get("forced")) or "forced" in str(title or "").lower()
        index = stream.get("index")
        tracks.append(
            EmbeddedTrack(
                stream_index=int(index) if index is not None else None,
                language=lang,
                codec=str(codec) if codec else None,
                kind=kind,
                extractable=extractable and index is not None,
                hi=hi,
                forced=forced,
                title=str(title) if title else None,
                source="ffprobe",
            )
        )
    return tracks


def pick_extractable_track(
    tracks: list[EmbeddedTrack],
    source_languages: list[str],
    *,
    target_language: str | None = None,
    allow_other_languages: bool = True,
) -> EmbeddedTrack | None:
    preferred: list[EmbeddedTrack] = []
    for track in tracks:
        if not track.extractable or track.stream_index is None:
            continue
        if not is_origin_language(
            track.language,
            preferred_languages=source_languages,
            target_language=target_language,
            allow_other_languages=allow_other_languages,
        ):
            continue
        preferred.append(track)
    if not preferred:
        return None
    # Prefer text over PGS OCR, then preferred language, non-forced, non-HI.
    preferred.sort(
        key=lambda t: (
            0 if t.kind == "text" else 1,
            origin_language_rank(t.language, source_languages),
            1 if t.forced else 0,
            0 if not t.hi else 1,
            t.stream_index or 999,
        )
    )
    return preferred[0]


async def extract_embedded_track(
    media_path: str | Path,
    stream_index: int,
    output_path: str | Path,
    *,
    language: str | None = "en",
    timeout: float | None = None,
    is_cancelled: CancelCheck | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Extract a text track with ffmpeg, or OCR a PGS image track to SRT."""
    tracks = await probe_subtitle_tracks(media_path, is_cancelled=is_cancelled)
    track = next((item for item in tracks if item.stream_index == stream_index), None)
    if track is None or track.kind == "text":
        return await extract_text_track(
            media_path,
            stream_index,
            output_path,
            timeout=timeout or TEXT_EXTRACT_TIMEOUT,
            is_cancelled=is_cancelled,
        )
    codec = (track.codec or "").strip().lower()
    if codec in PGS_CODECS:
        return await extract_pgs_track(
            media_path,
            stream_index,
            output_path,
            language=language or track.language or "en",
            timeout=timeout or PGS_EXTRACT_TIMEOUT,
            is_cancelled=is_cancelled,
            progress_callback=progress_callback,
        )
    raise EmbeddedError(
        f"Embedded subtitle codec {track.codec or 'unknown'} cannot be extracted."
    )


async def extract_pgs_track(
    media_path: str | Path,
    stream_index: int,
    output_path: str | Path,
    *,
    language: str | None = "en",
    timeout: float = PGS_EXTRACT_TIMEOUT,
    is_cancelled: CancelCheck | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    if not ocr_available():
        raise EmbeddedError(
            "Tesseract OCR is not installed; cannot extract image-based PGS subtitles."
        )
    media = Path(media_path)
    output = Path(output_path)
    if not media.is_file():
        raise EmbeddedError("Media file is not readable on disk.")
    if output.exists():
        raise EmbeddedError(f"Output subtitle already exists: {output.name}")

    temp_dir = Path(tempfile.mkdtemp(prefix="subtitle-ai-pgs-"))
    sup_path = temp_dir / "track.sup"
    cancel_flag = threading.Event()

    def ocr_cancelled() -> bool:
        return cancel_flag.is_set() or bool(is_cancelled and is_cancelled())

    def observe_cancelled_ocr(task: asyncio.Task[Path]) -> None:
        cancel_flag.set()

        def _consume(done: asyncio.Task[Path]) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.info("Cancelled PGS OCR worker ended: %s", exc)

        task.add_done_callback(_consume)

    try:
        await _demux_pgs_stream(
            media,
            stream_index,
            sup_path,
            timeout=min(timeout, 180.0),
            is_cancelled=is_cancelled,
        )
        sup_bytes = sup_path.read_bytes()
        if not sup_bytes:
            raise EmbeddedError("Demuxed PGS track was empty.")
        ocr_task = asyncio.create_task(
            asyncio.to_thread(
                pgs_sup_to_srt,
                sup_bytes,
                output,
                language=language,
                overwrite=False,
                is_cancelled=ocr_cancelled,
                progress_callback=progress_callback,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(ocr_task), timeout=timeout)
        except TimeoutError as exc:
            observe_cancelled_ocr(ocr_task)
            raise EmbeddedError(f"PGS OCR timed out for {media.name}") from exc
        except asyncio.CancelledError:
            observe_cancelled_ocr(ocr_task)
            raise
        except OcrError as exc:
            raise EmbeddedError(str(exc)) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _demux_pgs_stream(
    media: Path,
    stream_index: int,
    sup_path: Path,
    *,
    timeout: float,
    is_cancelled: CancelCheck | None = None,
) -> None:
    if not shutil.which("ffmpeg"):
        raise EmbeddedError("ffmpeg is not installed")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(media),
        "-map",
        f"0:{stream_index}",
        "-c",
        "copy",
        str(sup_path),
    ]
    try:
        result = await run_process_checked(
            command,
            timeout_s=timeout,
            is_cancelled=is_cancelled,
            output_paths=[sup_path],
        )
    except ProcessError as exc:
        if exc.outcome is ProcessOutcome.CANCELLED:
            raise asyncio.CancelledError from exc
        if exc.outcome is ProcessOutcome.TIMEOUT:
            raise EmbeddedError(f"ffmpeg PGS demux timed out for {media.name}") from exc
        raise EmbeddedError(f"ffmpeg PGS demux failed: {exc.stderr or 'empty output'}") from exc

    if result.returncode != 0 or not sup_path.exists() or sup_path.stat().st_size == 0:
        detail = result.stderr_text[-400:]
        raise EmbeddedError(f"ffmpeg PGS demux failed: {detail or 'empty output'}")


async def extract_text_track(
    media_path: str | Path,
    stream_index: int,
    output_path: str | Path,
    *,
    timeout: float = TEXT_EXTRACT_TIMEOUT,
    is_cancelled: CancelCheck | None = None,
) -> Path:
    media = Path(media_path)
    output = Path(output_path)
    if not media.is_file():
        raise EmbeddedError("Media file is not readable on disk.")
    if not shutil.which("ffmpeg"):
        raise EmbeddedError("ffmpeg is not installed")
    if output.exists():
        raise EmbeddedError(f"Output subtitle already exists: {output.name}")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Use a real .srt suffix so ffmpeg can pick a muxer, and avoid writing
    # directly beside media when the path contains characters ffmpeg mishandles (+).
    temp_dir = Path(tempfile.mkdtemp(prefix="subtitle-ai-extract-"))
    temp_output = temp_dir / "extract.srt"
    try:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(media),
            "-map",
            f"0:{stream_index}",
            "-c:s",
            "srt",
            "-f",
            "srt",
            str(temp_output),
        ]
        try:
            result = await run_process_checked(
                command,
                timeout_s=timeout,
                is_cancelled=is_cancelled,
                output_paths=[temp_output],
            )
        except ProcessError as exc:
            if exc.outcome is ProcessOutcome.CANCELLED:
                raise asyncio.CancelledError from exc
            if exc.outcome is ProcessOutcome.TIMEOUT:
                raise EmbeddedError(f"ffmpeg extraction timed out for {media.name}") from exc
            raise EmbeddedError(f"ffmpeg extraction failed: {exc.stderr or 'empty output'}") from exc

        if result.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
            detail = result.stderr_text[-400:]
            raise EmbeddedError(f"ffmpeg extraction failed: {detail or 'empty output'}")

        # Atomic-ish replace onto the media directory
        staging = output.with_name(f".{output.name}.tmp")
        try:
            shutil.copyfile(temp_output, staging)
            staging.replace(output)
        finally:
            staging.unlink(missing_ok=True)
        return output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
