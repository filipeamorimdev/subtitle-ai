"""SRT parser."""

from __future__ import annotations

import re

from app.subtitles.models import SubtitleBlock, SubtitleDocument

TIMESTAMP_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


class SrtParseError(ValueError):
    pass


def parse_srt(content: str, encoding: str = "utf-8") -> SubtitleDocument:
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise SrtParseError("SRT content is empty")

    chunks = re.split(r"\n\s*\n", text)
    blocks: list[SubtitleBlock] = []

    for chunk in chunks:
        lines = [line for line in chunk.split("\n") if line is not None]
        # Drop trailing empty lines within chunk
        while lines and lines[-1].strip() == "":
            lines.pop()
        if not lines:
            continue
        if len(lines) < 2:
            raise SrtParseError(f"Malformed SRT block: {chunk!r}")

        index_line = lines[0].strip()
        if not index_line.isdigit():
            # Some files omit blank lines; try recovering if first line is index-like
            raise SrtParseError(f"Invalid block index: {index_line!r}")
        index = int(index_line)

        timing = lines[1].strip()
        match = TIMESTAMP_RE.match(timing)
        if not match:
            raise SrtParseError(f"Invalid timestamp line: {timing!r}")
        start, end = match.group(1), match.group(2)

        body_lines = lines[2:]
        if not body_lines:
            raise SrtParseError(f"Block {index} has no dialogue text")
        body = "\n".join(body_lines)

        blocks.append(
            SubtitleBlock(
                index=index,
                start=start,
                end=end,
                text=body,
                original_text=body,
            )
        )

    if not blocks:
        raise SrtParseError("No subtitle blocks found")

    return SubtitleDocument(format="srt", encoding=encoding, blocks=blocks)
