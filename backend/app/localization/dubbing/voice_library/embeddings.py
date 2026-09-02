"""Lightweight speaker embeddings for cue-to-character matching."""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

import numpy as np

EMBEDDING_SAMPLE_RATE = 16_000


def _read_mono_pcm(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"Expected 16-bit PCM in {path.name}")
    samples = array.array("h")
    samples.frombytes(frames)
    if channels > 1:
        samples = array.array("h", (samples[i] for i in range(0, len(samples), channels)))
    return rate, samples


def _resample_linear(samples: array.array, source_rate: int, target_rate: int) -> array.array:
    if source_rate == target_rate or not samples:
        return samples
    ratio = target_rate / float(source_rate)
    out_len = max(1, int(round(len(samples) * ratio)))
    out = array.array("h")
    for index in range(out_len):
        source_index = index / ratio
        left = int(math.floor(source_index))
        right = min(left + 1, len(samples) - 1)
        weight = source_index - left
        value = int(round(samples[left] * (1.0 - weight) + samples[right] * weight))
        out.append(max(-32768, min(32767, value)))
    return out


def embedding_from_wav(path: Path) -> list[float]:
    """Return a compact spectral fingerprint suitable for cosine matching."""
    rate, samples = _read_mono_pcm(path)
    if rate != EMBEDDING_SAMPLE_RATE:
        samples = _resample_linear(samples, rate, EMBEDDING_SAMPLE_RATE)
    if len(samples) < EMBEDDING_SAMPLE_RATE // 10:
        raise ValueError("Reference clip is too short for speaker matching.")
    data = np.asarray(samples, dtype=np.float32) / 32768.0
    frame = max(1, EMBEDDING_SAMPLE_RATE // 50)
    frames = [
        data[offset : offset + frame]
        for offset in range(0, len(data) - frame, frame)
    ]
    if not frames:
        frames = [data]
    matrix = np.stack(frames, axis=0)
    spectrum = np.abs(np.fft.rfft(matrix, axis=1)).mean(axis=0)
    spectrum = spectrum / (np.linalg.norm(spectrum) + 1e-8)
    return spectrum.astype(np.float32).tolist()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def mean_embedding(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        return []
    stacked = np.asarray(embeddings, dtype=np.float32)
    centroid = stacked.mean(axis=0)
    centroid = centroid / (np.linalg.norm(centroid) + 1e-8)
    return centroid.astype(np.float32).tolist()
