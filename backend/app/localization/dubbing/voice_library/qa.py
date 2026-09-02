"""Generated dub audio quality checks."""

from __future__ import annotations

import array
import wave
from dataclasses import dataclass
from pathlib import Path

from app.localization.dubbing.providers.chatterbox import tts_output_ignores_text, wav_duration_seconds
from app.localization.dubbing.voice_library.embeddings import cosine_similarity, embedding_from_wav


@dataclass(frozen=True)
class DubQualityReport:
    ok: bool
    reasons: list[str]


def _peak_amplitude(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    if not frames:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return 0.0
    return max(abs(value) for value in samples) / 32768.0


def validate_generated_cue(
    wav_path: Path,
    *,
    expected_text: str,
    reference_centroid: list[float] | None = None,
    min_duration: float = 0.05,
    min_similarity: float = 0.55,
) -> DubQualityReport:
    reasons: list[str] = []
    if not wav_path.is_file() or wav_path.stat().st_size < 64:
        reasons.append("empty_output")
    duration = wav_duration_seconds(wav_path) or 0.0
    if duration < min_duration:
        reasons.append("too_short")
    if _peak_amplitude(wav_path) >= 0.999:
        reasons.append("clipped")
    if len(expected_text.strip()) >= 12 and duration > 0 and duration < min(len(expected_text) * 0.02, 0.2):
        reasons.append("suspiciously_short_for_text")
    if reference_centroid:
        try:
            embedding = embedding_from_wav(wav_path)
            if cosine_similarity(embedding, reference_centroid) < min_similarity:
                reasons.append("identity_mismatch")
        except ValueError:
            reasons.append("identity_check_failed")
    return DubQualityReport(ok=not reasons, reasons=reasons)


def validate_batch_lengths(samples: list[tuple[int, float]]) -> DubQualityReport:
    if tts_output_ignores_text(samples):
        return DubQualityReport(ok=False, reasons=["looping_output"])
    return DubQualityReport(ok=True, reasons=[])
