"""Audio separation domain models and errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.localization.artifacts import AudioArtifact
from app.localization.audio.debug import DebugTrace

CancelCheck = Callable[[], bool]

FEATURE_NAME = "audio_separation"
DEBUG_FEATURE = "audio-separation"
PROVIDER_DEMUCS = "demucs"
DEFAULT_DEMUCS_MODEL = "htdemucs"
ROLE_DIALOGUE = "dialogue"
ROLE_BACKGROUND = "background"

# Accept small encoder/model hop-size drift; reject clearly wrong lengths.
MIN_DURATION_TOLERANCE_S = 0.35
MAX_DURATION_TOLERANCE_S = 2.0
DURATION_TOLERANCE_RATIO = 0.02

CODE_PROVIDER_UNAVAILABLE = "provider_unavailable"
CODE_INPUT_PREPARATION_FAILED = "input_preparation_failed"
CODE_SEPARATION_FAILED = "separation_failed"
CODE_CANCELLED = "cancelled"
CODE_MISSING_DIALOGUE = "missing_dialogue_output"
CODE_MISSING_BACKGROUND = "missing_background_output"
CODE_INVALID_OUTPUT = "invalid_output"
CODE_VERIFICATION_FAILED = "verification_failed"


class AudioSeparationError(Exception):
    """Focused failure for the isolated audio-separation feature."""

    def __init__(
        self,
        message: str,
        *,
        code: str = CODE_SEPARATION_FAILED,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass
class SeparationResult:
    dialogue: AudioArtifact
    background: AudioArtifact
    provider: str
    model: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    debug_trace_path: Path | None = None


@dataclass(frozen=True)
class AudioFileInfo:
    path: Path
    exists: bool
    size: int
    has_audio: bool
    duration: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


class AudioSeparationProvider(Protocol):
    """Minimal provider boundary so orchestration is not tied to Demucs."""

    name: str
    model: str | None

    async def separate(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        debug: DebugTrace | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> SeparationResult:
        ...


def duration_tolerance_s(input_duration: float) -> float:
    ratio = abs(input_duration) * DURATION_TOLERANCE_RATIO
    return max(MIN_DURATION_TOLERANCE_S, min(MAX_DURATION_TOLERANCE_S, ratio))
