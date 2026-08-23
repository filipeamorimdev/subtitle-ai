"""PCM audio timeline that mixes overlapping clips without ffmpeg adelay."""

from __future__ import annotations

import array
import math
import wave
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("dubbing.timeline")

CUE_SAMPLE_RATE = 16000
MAX_TTS_TIMELINE_HOURS = 6.0
CLIP_PEAK_DBFS = -3.0
MIX_PEAK_DBFS = -1.0
LIMITER_CEILING = 0.99


@dataclass
class TimelineClip:
    path: Path
    start_s: float
    speaker_id: str | None = None
    samples: array.array = field(default_factory=lambda: array.array("h"))
    sample_rate: int = CUE_SAMPLE_RATE


def _db_to_peak(dbfs: float) -> int:
    return max(1, int(round(32767 * (10 ** (dbfs / 20.0)))))


def _read_pcm_s16_mono(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"Expected 16-bit PCM in {path.name}, got {width * 8}-bit")
    if rate <= 0:
        raise ValueError(f"Invalid sample rate in {path.name}")
    samples = array.array("h")
    samples.frombytes(frames)
    if channels <= 0:
        raise ValueError(f"No audio channels in {path.name}")
    if channels > 1:
        samples = array.array("h", (samples[index] for index in range(0, len(samples), channels)))
    return rate, samples


def _peak_normalize(samples: array.array, target_peak: int) -> array.array:
    if not samples:
        return samples
    peak = max(abs(value) for value in samples)
    if peak <= 0:
        return samples
    scale = target_peak / peak
    if abs(scale - 1.0) < 0.01:
        return samples
    out = array.array("h")
    for value in samples:
        scaled = int(round(value * scale))
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out.append(scaled)
    return out


def _limit(samples: array.array, ceiling: float = LIMITER_CEILING) -> array.array:
    limit = int(32767 * ceiling)
    out = array.array("h")
    for value in samples:
        if value > limit:
            out.append(limit)
        elif value < -limit:
            out.append(-limit)
        else:
            out.append(value)
    return out


class AudioTimeline:
    def __init__(self, *, sample_rate: int = CUE_SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.clips: list[TimelineClip] = []
        self.overlap_count = 0

    def add_clip(self, path: str | Path, start_s: float, *, speaker_id: str | None = None) -> None:
        clip_path = Path(path)
        rate, samples = _read_pcm_s16_mono(clip_path)
        if rate != self.sample_rate:
            raise ValueError(f"Cue clip {clip_path.name} is {rate} Hz, expected {self.sample_rate} Hz")
        start = max(0.0, start_s)
        for existing in self.clips:
            existing_end = existing.start_s + (len(existing.samples) / self.sample_rate)
            clip_end = start + (len(samples) / self.sample_rate)
            if start < existing_end and clip_end > existing.start_s:
                self.overlap_count += 1
        self.clips.append(
            TimelineClip(
                path=clip_path,
                start_s=start,
                speaker_id=speaker_id,
                samples=_peak_normalize(samples, _db_to_peak(CLIP_PEAK_DBFS)),
                sample_rate=rate,
            )
        )

    def mix(self, *, media_duration_s: float | None = None) -> array.array:
        if not self.clips:
            raise ValueError("No cue clips to mix")
        last_sample = 0
        placed: list[tuple[int, array.array]] = []
        for clip in self.clips:
            start = max(0, int(round(clip.start_s * self.sample_rate)))
            last_sample = max(last_sample, start + len(clip.samples))
            placed.append((start, clip.samples))
        total_samples = last_sample
        if media_duration_s is not None and media_duration_s > 0:
            total_samples = max(total_samples, int(round(media_duration_s * self.sample_rate)))
        max_samples = int(MAX_TTS_TIMELINE_HOURS * 3600 * self.sample_rate)
        if total_samples > max_samples:
            raise ValueError(
                f"TTS timeline is too long ({total_samples / self.sample_rate:.1f}s, "
                f"max {MAX_TTS_TIMELINE_HOURS:.0f}h)"
            )
        mix = [0.0] * total_samples
        for start, samples in placed:
            for offset, value in enumerate(samples):
                index = start + offset
                if index >= total_samples:
                    break
                mix[index] += value
        logger.info(
            "timeline_mixed clips=%s overlaps=%s duration=%.2f",
            len(self.clips),
            self.overlap_count,
            total_samples / self.sample_rate,
        )
        peak = max((abs(sample) for sample in mix), default=0.0)
        target = _db_to_peak(MIX_PEAK_DBFS)
        scale = (target / peak) if peak > 0 else 1.0
        limited = int(32767 * LIMITER_CEILING)
        out = array.array("h")
        for sample in mix:
            value = int(round(sample * scale))
            if value > limited:
                value = limited
            elif value < -limited:
                value = -limited
            out.append(value)
        return out

    def normalize(self, samples: array.array) -> array.array:
        return _peak_normalize(samples, _db_to_peak(MIX_PEAK_DBFS))

    def limit(self, samples: array.array) -> array.array:
        return _limit(samples)

    def render(self, output_path: str | Path, *, media_duration_s: float | None = None) -> Path:
        mixed = self.mix(media_duration_s=media_duration_s)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(mixed.tobytes())
        return output


def write_tts_timeline_wav(
    shaped_clips: list[tuple[Path, int]],
    output_wav: Path,
    *,
    media_duration_s: float | None = None,
    sample_rate: int = CUE_SAMPLE_RATE,
) -> None:
    """Compatibility wrapper: (path, start_ms) clips on a mixed PCM timeline."""
    timeline = AudioTimeline(sample_rate=sample_rate)
    for clip_path, start_ms in shaped_clips:
        timeline.add_clip(clip_path, start_ms / 1000.0)
    timeline.render(output_wav, media_duration_s=media_duration_s)
    if timeline.overlap_count:
        logger.info("timeline_overlaps count=%s", timeline.overlap_count)


# Keep math import for potential loudness helpers.
_ = math
