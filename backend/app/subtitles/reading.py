"""Reading-speed and line-length checks for translated subtitles."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.subtitles.models import SubtitleBlock, SubtitleDocument

MAX_CPS = 17.0
MAX_LINES = 2
MAX_LINE_CHARS = 42
MIN_DURATION_MS = 800

_TIME_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
)


def parse_srt_timestamp(value: str) -> int | None:
    match = _TIME_RE.search(value or "")
    if not match:
        return None
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = int(match.group("ms").ljust(3, "0")[:3])
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def duration_ms(block: SubtitleBlock) -> int | None:
    start = parse_srt_timestamp(block.start)
    end = parse_srt_timestamp(block.end)
    if start is None or end is None or end <= start:
        return None
    return end - start


def visible_text(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", "", text or "")
    stripped = re.sub(r"\{[^}]+\}", "", stripped)
    return stripped.replace("\n", " ").strip()


@dataclass(frozen=True)
class ReadingIssue:
    block_index: int
    kind: str
    message: str
    cps: float | None = None
    duration_ms: int | None = None


def analyze_block(block: SubtitleBlock) -> list[ReadingIssue]:
    issues: list[ReadingIssue] = []
    lines = [line.rstrip() for line in (block.text or "").split("\n") if line.strip()]
    if len(lines) > MAX_LINES:
        issues.append(
            ReadingIssue(
                block.index,
                "too_many_lines",
                f"Block {block.index} has {len(lines)} lines (max {MAX_LINES}).",
            )
        )
    for line in lines:
        plain = visible_text(line)
        if len(plain) > MAX_LINE_CHARS:
            issues.append(
                ReadingIssue(
                    block.index,
                    "line_too_long",
                    f"Block {block.index} has a line of {len(plain)} characters "
                    f"(max {MAX_LINE_CHARS}).",
                )
            )
    dur = duration_ms(block)
    chars = len(visible_text(block.text))
    cps = None
    if dur and dur > 0:
        cps = chars / (dur / 1000.0)
        if cps > MAX_CPS:
            issues.append(
                ReadingIssue(
                    block.index,
                    "cps_too_high",
                    f"Block {block.index} reads at {cps:.1f} cps (max {MAX_CPS:.0f}).",
                    cps=cps,
                    duration_ms=dur,
                )
            )
    return issues


def analyze_document(document: SubtitleDocument) -> list[ReadingIssue]:
    issues: list[ReadingIssue] = []
    for block in document.blocks:
        issues.extend(analyze_block(block))
    return issues


def overcrowded_blocks(
    document: SubtitleDocument, *, limit: int = 40
) -> list[SubtitleBlock]:
    flagged: list[SubtitleBlock] = []
    seen: set[int] = set()
    for issue in analyze_document(document):
        if issue.block_index in seen:
            continue
        seen.add(issue.block_index)
        block = next((b for b in document.blocks if b.index == issue.block_index), None)
        if block is not None:
            flagged.append(block)
        if len(flagged) >= limit:
            break
    return flagged


def reading_repair_prompt_extra() -> str:
    return (
        f"\nSome lines are too long to read on screen. Shorten ONLY the blocks below "
        f"so each cue has at most {MAX_LINES} lines, each line at most {MAX_LINE_CHARS} "
        f"characters, and a comfortable reading speed (about {int(MAX_CPS)} characters "
        f"per second). Keep meaning, placeholders like <TAG0>, and the same IDs. "
        f"Do not merge or split blocks.\n"
    )
