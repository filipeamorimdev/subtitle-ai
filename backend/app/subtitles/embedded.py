"""Probe and extract embedded subtitle tracks via ffprobe/ffmpeg."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from app.subtitles.filenames import language_matches, normalize_language_code

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

IMAGE_CODECS = {
    "hdmv_pgs_subtitle",
    "pgssub",
    "dvd_subtitle",
    "dvdsub",
    "dvb_subtitle",
    "xsub",
    "vobsub",
}


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


async def probe_subtitle_tracks(media_path: str | Path, *, timeout: float = 12.0) -> list[EmbeddedTrack]:
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
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        raise EmbeddedError(f"ffprobe timed out for {path.name}") from exc
    except FileNotFoundError as exc:
        raise EmbeddedError("ffprobe is not installed") from exc

    if proc.returncode != 0:
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
) -> EmbeddedTrack | None:
    preferred: list[EmbeddedTrack] = []
    for track in tracks:
        if not track.extractable or track.stream_index is None:
            continue
        if track.language and language_matches(track.language, source_languages):
            preferred.append(track)
        elif track.language is None and source_languages:
            # Undetermined language — allow as last resort
            preferred.append(track)
    if not preferred:
        return None
    # Prefer non-forced, matching language, then lower stream index
    preferred.sort(
        key=lambda t: (
            0 if t.language and language_matches(t.language, source_languages) else 1,
            1 if t.forced else 0,
            0 if not t.hi else 1,
            t.stream_index or 999,
        )
    )
    return preferred[0]


async def extract_text_track(
    media_path: str | Path,
    stream_index: int,
    output_path: str | Path,
    *,
    timeout: float = 300.0,
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
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            raise EmbeddedError(f"ffmpeg extraction timed out for {media.name}") from exc
        except FileNotFoundError as exc:
            raise EmbeddedError("ffmpeg is not installed") from exc

        if proc.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
            detail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
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