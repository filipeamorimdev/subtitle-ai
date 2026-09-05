"""Generated dub audio quality checks."""

from __future__ import annotations

import array
import wave
from dataclasses import dataclass
from pathlib import Path

from app.localization.dubbing.providers.chatterbox import tts_output_ignores_text, wav_duration_seconds
from app.localization.dubbing.voice_library.embeddings import cosine_similarity, embedding_from_wav

_CLIP_AMPLITUDE = 0.999
_CLIP_RATIO = 0.01
_PEAK_CEILING = 0.95


@dataclass(frozen=True)
class DubQualityReport:
    ok: bool
    reasons: list[str]


def _pcm16_samples(path: Path) -> tuple[array.array, object | None]:
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    samples = array.array("h")
    if frames:
        samples.frombytes(frames)
    return samples, params


def _clip_ratio(path: Path) -> float:
    samples, _params = _pcm16_samples(path)
    if not samples:
        return 0.0
    threshold = int(_CLIP_AMPLITUDE * 32768)
    clipped = sum(1 for value in samples if abs(value) >= threshold)
    return clipped / len(samples)


def peak_limit_wav(path: Path, ceiling: float = _PEAK_CEILING) -> bool:
    """Scale a PCM16 cue so its peak stays below ``ceiling``. Returns True if rewritten."""
    samples, params = _pcm16_samples(path)
    if not samples or params is None:
        return False
    peak = max(abs(value) for value in samples)
    limit = int(ceiling * 32768)
    if peak <= limit:
        return False
    scale = limit / peak
    limited = array.array("h", (int(value * scale) for value in samples))
    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(limited.tobytes())
    return True


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
    # Isolated full-scale samples are normal after PCM16 quantization. Fail
    # only when a meaningful slice of the cue is slammed against the rail.
    if _clip_ratio(wav_path) >= _CLIP_RATIO:
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
