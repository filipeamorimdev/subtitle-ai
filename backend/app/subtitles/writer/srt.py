"""SRT writer and atomic file output."""

from __future__ import annotations

import os
from pathlib import Path

from app.subtitles.models import SubtitleDocument


def render_srt(document: SubtitleDocument) -> str:
    parts: list[str] = []
    for block in document.blocks:
        parts.append(str(block.index))
        parts.append(f"{block.start} --> {block.end}")
        parts.append(block.text)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_srt_atomic(path: Path, document: SubtitleDocument, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Target subtitle already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_srt(document)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding=document.encoding, newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
