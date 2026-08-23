"""Small synthetic media fixtures. No copyrighted movie files."""

from __future__ import annotations

import array
import math
import shutil
import subprocess
import wave
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def write_sine_wav(
    path: Path,
    *,
    duration_s: float,
    frequency: float,
    sample_rate: int = 16000,
    amplitude: int = 12000,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = max(1, int(duration_s * sample_rate))
    samples = array.array("h")
    for index in range(n_frames):
        value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        samples.append(value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return path


def write_silent_wav(path: Path, *, duration_s: float, sample_rate: int = 16000) -> Path:
    return write_sine_wav(path, duration_s=duration_s, frequency=0.0, amplitude=0, sample_rate=sample_rate)


def build_multitrack_mkv(output: Path, workdir: Path) -> Path:
    """English default  + English commentary + Portuguese audio under a tiny video."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg/ffprobe are required to build the multitrack fixture")
    en = write_sine_wav(workdir / "english_dialogue.wav", duration_s=2.0, frequency=440)
    commentary = write_sine_wav(workdir / "english_commentary.wav", duration_s=2.0, frequency=880)
    pt = write_sine_wav(workdir / "portuguese.wav", duration_s=2.0, frequency=330)
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120:d=2",
        "-i",
        str(en),
        "-i",
        str(commentary),
        "-i",
        str(pt),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map",
        "2:a:0",
        "-map",
        "3:a:0",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-metadata:s:a:0",
        "language=eng",
        "-disposition:a:0",
        "default",
        "-metadata:s:a:1",
        "language=eng",
        "-metadata:s:a:1",
        "title=Commentary",
        "-disposition:a:1",
        "comment",
        "-metadata:s:a:2",
        "language=por",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return output
