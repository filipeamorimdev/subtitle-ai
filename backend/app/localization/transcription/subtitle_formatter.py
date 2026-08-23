"""Convert a structured transcript into subtitle cues."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.localization.transcription.models import Transcript, TranscriptWord
from app.subtitles.models import SubtitleBlock, SubtitleDocument

logger = get_logger("subtitle_formatter")

HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "st", "prof", "sr", "sra", "srta"})
STRONG_PUNCT = frozenset(".?!")
MEDIUM_PUNCT = frozenset(";:")
WEAK_PUNCT = frozenset(",")


@dataclass(frozen=True)
class FormatterConfig:
    max_lines: int = 2
    max_chars_per_line: int = 42
    max_cps: float = 20.0
    min_duration: float = 0.8
    max_duration: float = 7.0
    minimum_gap: float = 0.08


@dataclass
class FormatterStats:
    cues_created: int = 0
    merged_short: int = 0
    split_long: int = 0
    gap_adjusted: int = 0
    orphan_absorbed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "cues_created": self.cues_created,
            "merged_short": self.merged_short,
            "split_long": self.split_long,
            "gap_adjusted": self.gap_adjusted,
            "orphan_absorbed": self.orphan_absorbed,
        }


def seconds_to_srt_timestamp(value: float) -> str:
    ms = int(round(max(0.0, value) * 1000.0))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _clean_word(text: str) -> str:
    return (text or "").replace("\n", " ").strip()


def _words_from_transcript(transcript: Transcript) -> list[TranscriptWord]:
    words: list[TranscriptWord] = []
    for segment in transcript.segments:
        if float(getattr(segment, "no_speech_prob", 0.0) or 0.0) >= 0.65:
            continue
        if segment.words:
            for word in segment.words:
                text = _clean_word(word.text)
                if not text:
                    continue
                end = word.end if word.end > word.start else word.start + 0.05
                words.append(TranscriptWord(start=word.start, end=end, text=text, probability=word.probability))
            continue
        text = " ".join((segment.text or "").split())
        if not text:
            continue
        parts = text.split()
        start = float(segment.start)
        end = float(segment.end if segment.end > segment.start else segment.start + 0.5)
        span = max(0.05, end - start)
        if len(parts) == 1:
            words.append(TranscriptWord(start=start, end=end, text=parts[0]))
            continue
        weights = [max(1, len(part)) for part in parts]
        total = sum(weights)
        cursor = start
        for index, part in enumerate(parts):
            piece = span * (weights[index] / total)
            piece_end = end if index == len(parts) - 1 else cursor + piece
            words.append(TranscriptWord(start=cursor, end=piece_end, text=part))
            cursor = piece_end
    return words


def _ends_with_punct(text: str, marks: frozenset[str]) -> bool:
    stripped = text.rstrip('"').rstrip("'").rstrip()
    return bool(stripped) and stripped[-1] in marks


def _is_honorific(text: str) -> bool:
    token = re.sub(r"[^\w]", "", text).lower()
    return token in HONORIFICS


def _looks_like_name_pair(left: str, right: str) -> bool:
    if _is_honorific(left):
        return True
    left_clean = re.sub(r"[^\w\-]", "", left)
    right_clean = re.sub(r"[^\w\-]", "", right)
    return (
        bool(left_clean)
        and bool(right_clean)
        and left_clean[0].isupper()
        and right_clean[0].isupper()
        and not _ends_with_punct(left, STRONG_PUNCT)
    )


def _join(words: list[TranscriptWord]) -> str:
    return " ".join(word.text for word in words).strip()


def _wrap_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    tokens = text.split()
    if not tokens:
        return []
    lines: list[str] = []
    current: list[str] = []
    for token in tokens:
        trial = " ".join(current + [token]) if current else token
        if len(trial) <= max_chars or not current:
            current.append(token)
            continue
        lines.append(" ".join(current))
        current = [token]
        if len(lines) >= max_lines:
            # Remaining tokens belong on the last allowed line; caller splits cues.
            extra = " ".join(current)
            if extra:
                lines.append(extra)
            return lines
    if current:
        lines.append(" ".join(current))
    return lines


def _fits(words: list[TranscriptWord], config: FormatterConfig) -> bool:
    if not words:
        return False
    text = _join(words)
    duration = max(0.001, words[-1].end - words[0].start)
    if duration > config.max_duration + 0.05:
        return False
    lines = _wrap_lines(text, config.max_chars_per_line, config.max_lines)
    if len(lines) > config.max_lines:
        return False
    if any(len(line) > config.max_chars_per_line + 8 for line in lines):
        return False
    chars = len(text.replace(" ", "")) + text.count(" ")
    cps = chars / duration
    if cps > config.max_cps and duration >= config.max_duration:
        return False
    return True


def _needed_duration(text: str, config: FormatterConfig, natural: float) -> float:
    chars = len(text)
    min_for_cps = chars / config.max_cps if config.max_cps > 0 else 0.0
    duration = max(config.min_duration, natural, min_for_cps)
    return min(duration, config.max_duration)


def _split_oversize(words: list[TranscriptWord], config: FormatterConfig, stats: FormatterStats) -> list[list[TranscriptWord]]:
    if _fits(words, config):
        return [words]
    stats.split_long += 1
    if len(words) <= 1:
        return [words]
    # Prefer punctuation, then midpoint, never leaving an honorific hanging.
    best = len(words) // 2
    for index in range(len(words) - 1, 0, -1):
        if _is_honorific(words[index - 1].text):
            continue
        if _ends_with_punct(words[index - 1].text, STRONG_PUNCT | MEDIUM_PUNCT | WEAK_PUNCT):
            best = index
            break
    if best <= 0 or best >= len(words):
        best = max(1, len(words) // 2)
    left = words[:best]
    right = words[best:]
    parts: list[list[TranscriptWord]] = []
    parts.extend(_split_oversize(left, config, stats) if not _fits(left, config) else [left])
    parts.extend(_split_oversize(right, config, stats) if not _fits(right, config) else [right])
    return parts


def _group_words(words: list[TranscriptWord], config: FormatterConfig, stats: FormatterStats) -> list[list[TranscriptWord]]:
    groups: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []
    index = 0
    while index < len(words):
        word = words[index]
        trial = current + [word]
        if current and not _fits(trial, config):
            groups.extend(_split_oversize(current, config, stats))
            current = []
            continue
        if not current and not _fits(trial, config):
            groups.extend(_split_oversize(trial, config, stats))
            index += 1
            continue
        current = trial
        # Prefer ending the cue at a sentence boundary once it is reasonably long.
        text = _join(current)
        duration = current[-1].end - current[0].start
        next_word = words[index + 1] if index + 1 < len(words) else None
        if (
            current
            and _ends_with_punct(word.text, STRONG_PUNCT)
            and not _is_honorific(word.text)
            and (len(text) >= config.max_chars_per_line or duration >= 1.2)
        ):
            groups.extend(_split_oversize(current, config, stats))
            current = []
        elif (
            next_word is not None
            and current
            and _looks_like_name_pair(word.text, next_word.text)
            and _fits(current + [next_word], config)
        ):
            # Keep "Mr. Smith" / "John Smith" together.
            pass
        index += 1
    if current:
        groups.extend(_split_oversize(current, config, stats))

    # Absorb orphan last word of a trailing 1-word cue into the previous cue when possible.
    merged: list[list[TranscriptWord]] = []
    for group in groups:
        if merged and len(group) == 1 and _fits(merged[-1] + group, config):
            merged[-1] = merged[-1] + group
            stats.orphan_absorbed += 1
            continue
        merged.append(group)
    return merged


def _merge_short(groups: list[list[TranscriptWord]], config: FormatterConfig, stats: FormatterStats) -> list[list[TranscriptWord]]:
    if not groups:
        return groups
    result: list[list[TranscriptWord]] = [groups[0]]
    for group in groups[1:]:
        prev = result[-1]
        prev_dur = prev[-1].end - prev[0].start
        group_dur = group[-1].end - group[0].start
        combined = prev + group
        if (
            (prev_dur < config.min_duration or group_dur < config.min_duration)
            and _fits(combined, config)
            and (combined[-1].end - combined[0].start) <= config.max_duration
        ):
            result[-1] = combined
            stats.merged_short += 1
            continue
        result.append(group)
    return result


def _apply_timing(groups: list[list[TranscriptWord]], config: FormatterConfig, stats: FormatterStats) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for group in groups:
        text = _join(group)
        start = group[0].start
        natural = max(0.05, group[-1].end - start)
        end = start + _needed_duration(text, config, natural)
        cues.append((start, end, text))

    adjusted: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        if adjusted:
            prev_start, prev_end, prev_text = adjusted[-1]
            gap_needed = prev_end + config.minimum_gap
            if start < gap_needed:
                stats.gap_adjusted += 1
                # Shrink previous cue if it still meets min duration; otherwise push this start.
                shrunk = start - config.minimum_gap
                if shrunk - prev_start >= config.min_duration:
                    adjusted[-1] = (prev_start, shrunk, prev_text)
                else:
                    start = gap_needed
                    if end <= start:
                        end = start + config.min_duration
        if end <= start:
            end = start + config.min_duration
        adjusted.append((start, end, text))

    # Final monotonic pass
    monotonic: list[tuple[float, float, str]] = []
    for start, end, text in adjusted:
        if monotonic and start < monotonic[-1][1]:
            start = monotonic[-1][1] + config.minimum_gap
            stats.gap_adjusted += 1
        if end <= start:
            end = start + config.min_duration
        monotonic.append((max(0.0, start), end, text))
    return monotonic


def format_lines(text: str, config: FormatterConfig) -> str:
    lines = _wrap_lines(text, config.max_chars_per_line, config.max_lines)
    if len(lines) > config.max_lines:
        # Keep the first max_lines; remaining words stay on the last line (cue should have been split).
        head = lines[: config.max_lines - 1]
        tail = " ".join(lines[config.max_lines - 1 :])
        lines = head + [tail]
    return "\n".join(line.strip() for line in lines if line.strip())


class SubtitleFormatter:
    def __init__(self, config: FormatterConfig | None = None) -> None:
        self.config = config or FormatterConfig()

    def format(self, transcript: Transcript) -> tuple[SubtitleDocument, FormatterStats]:
        stats = FormatterStats()
        words = _words_from_transcript(transcript)
        if not words:
            raise ValueError("Transcription produced no usable speech segments.")
        groups = _group_words(words, self.config, stats)
        groups = _merge_short(groups, self.config, stats)
        timed = _apply_timing(groups, self.config, stats)
        blocks = [
            SubtitleBlock(
                index=index,
                start=seconds_to_srt_timestamp(start),
                end=seconds_to_srt_timestamp(end),
                text=format_lines(text, self.config),
                original_text=text,
            )
            for index, (start, end, text) in enumerate(timed, start=1)
            if text.strip()
        ]
        if not blocks:
            raise ValueError("Transcription produced no usable speech segments.")
        stats.cues_created = len(blocks)
        logger.info(
            "subtitle_formatted cues=%s merged_short=%s split_long=%s gap_adjusted=%s orphan_absorbed=%s",
            stats.cues_created,
            stats.merged_short,
            stats.split_long,
            stats.gap_adjusted,
            stats.orphan_absorbed,
        )
        return SubtitleDocument(format="srt", encoding="utf-8", blocks=blocks), stats
