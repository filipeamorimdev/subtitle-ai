"""Persistent per-cue checkpoints for long-running CPU dub jobs."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from app.localization.dubbing.timeline import CUE_SAMPLE_RATE
from app.localization.dubbing.timing import TimingDecision, TimingEngine

CACHE_SCHEMA = 2


@dataclass(frozen=True)
class CachedCue:
    path: Path
    actual: float
    decision: TimingDecision


def dub_cache_key(
    *,
    source_srt: str,
    target_language: str,
    voice_model: str,
    speaker_voice_overrides: dict[str, str],
    voice_bindings: dict[str, str] | None,
    timing: TimingEngine,
) -> str:
    """Hash every input that can change a shaped cue's audio or placement."""
    payload = {
        "schema": CACHE_SCHEMA,
        "source_srt": source_srt,
        "target_language": target_language,
        "voice_model": voice_model,
        "speaker_voice_overrides": speaker_voice_overrides,
        "voice_bindings": voice_bindings or {},
        "timing": {"max_speed": timing.max_speed, "min_speed": timing.min_speed},
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DubCueCache:
    """Store shaped PCM cue WAVs atomically so a new job can resume safely."""

    def __init__(self, root: Path, key: str) -> None:
        self.path = root / key
        self.path.mkdir(parents=True, exist_ok=True)

    def _paths(self, index: int) -> tuple[Path, Path]:
        stem = f"cue-{index:05d}"
        return self.path / f"{stem}.wav", self.path / f"{stem}.json"

    def load(self, index: int) -> CachedCue | None:
        wav_path, metadata_path = self._paths(index)
        if not wav_path.is_file() or wav_path.stat().st_size < 64 or not metadata_path.is_file():
            return None
        try:
            with wave.open(str(wav_path), "rb") as handle:
                if (
                    handle.getnchannels() != 1
                    or handle.getsampwidth() != 2
                    or handle.getframerate() != CUE_SAMPLE_RATE
                    or handle.getnframes() <= 0
                ):
                    return None
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            if payload.get("schema") != CACHE_SCHEMA or payload.get("index") != index:
                return None
            decision = TimingDecision(**payload["decision"])
            actual = float(payload["actual"])
        except (OSError, ValueError, TypeError, KeyError, EOFError, json.JSONDecodeError, wave.Error):
            return None
        return CachedCue(path=wav_path, actual=actual, decision=decision)

    def store(self, index: int, shaped_wav: Path, *, actual: float, decision: TimingDecision) -> Path:
        wav_path, metadata_path = self._paths(index)
        token = uuid.uuid4().hex
        wav_staging = wav_path.with_name(f".{wav_path.name}.{token}.tmp")
        metadata_staging = metadata_path.with_name(f".{metadata_path.name}.{token}.tmp")
        try:
            shutil.copyfile(shaped_wav, wav_staging)
            wav_staging.replace(wav_path)
            metadata_staging.write_text(
                json.dumps(
                    {
                        "schema": CACHE_SCHEMA,
                        "index": index,
                        "actual": actual,
                        "decision": asdict(decision),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            metadata_staging.replace(metadata_path)
        finally:
            wav_staging.unlink(missing_ok=True)
            metadata_staging.unlink(missing_ok=True)
        return wav_path

    def clear(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
