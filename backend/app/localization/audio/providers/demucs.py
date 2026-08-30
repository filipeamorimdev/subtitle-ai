"""Local Demucs two-stem provider (vocals vs accompaniment)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.localization.artifacts import AudioArtifact, path_str
from app.localization.audio.debug import DebugTrace, run_logged_process
from app.localization.audio.models import (
    CODE_CANCELLED,
    CODE_MISSING_BACKGROUND,
    CODE_MISSING_DIALOGUE,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_SEPARATION_FAILED,
    DEFAULT_DEMUCS_MODEL,
    PROVIDER_DEMUCS,
    ROLE_BACKGROUND,
    ROLE_DIALOGUE,
    AudioSeparationError,
    CancelCheck,
    SeparationResult,
)
from app.media.process_runner import ProcessOutcome

logger = get_logger("audio_separation.demucs")

SEPARATION_TIMEOUT_S = 8 * 3600.0
VOCALS_STEM = "vocals"
ACCOMPANIMENT_STEM = "no_vocals"


def demucs_cache_dir() -> Path:
    path = get_app_config().config_dir / "demucs-models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def demucs_available() -> bool:
    return shutil.which("demucs") is not None or importlib.util.find_spec("demucs") is not None


def demucs_argv() -> list[str]:
    found = shutil.which("demucs")
    if found:
        return [found]
    return [sys.executable, "-m", "demucs"]


def build_demucs_command(
    input_path: Path,
    output_dir: Path,
    *,
    model: str,
    device: str = "cpu",
) -> list[str]:
    return [
        *demucs_argv(),
        "-n",
        model,
        "-d",
        device,
        "--two-stems",
        "vocals",
        "-j",
        "1",
        "-o",
        str(output_dir),
        str(input_path),
    ]


def _cpu_threads() -> int:
    configured = int(getattr(get_app_config(), "whisper_cpu_threads", 0) or 0)
    if configured > 0:
        return configured
    count = os.cpu_count() or 2
    if count <= 2:
        return 1
    return max(1, count // 2)


def _stem_path(root: Path, model: str, track: str, name: str) -> Path | None:
    candidates = [
        root / model / track / f"{name}.wav",
        root / track / f"{name}.wav",
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    matches = [path for path in root.rglob(f"{name}.wav") if path.is_file()]
    return matches[0] if matches else None


def _copy_stem(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    return dest


def _artifact(path: Path, *, role: str, model: str, source: Path) -> AudioArtifact:
    size = path.stat().st_size if path.is_file() else 0
    return AudioArtifact(
        path=path_str(path),
        provider=PROVIDER_DEMUCS,
        metadata={
            "role": role,
            "model": model,
            "source_path": str(source),
            "size": size,
        },
    )


def _failure_detail(result: object) -> str:
    """Return a compact terminal diagnostic without burying the job message."""
    stderr = getattr(result, "stderr_text", "") or ""
    stdout = getattr(result, "stdout_text", "") or ""
    text = " ".join((stderr or stdout).split())
    return text[-600:]


class DemucsProvider:
    name = PROVIDER_DEMUCS

    def __init__(self, *, model: str = DEFAULT_DEMUCS_MODEL, device: str = "cpu") -> None:
        self.model = model
        self.device = device

    async def separate(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        debug: DebugTrace | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> SeparationResult:
        source = Path(input_path)
        dest = Path(output_dir)
        dest.mkdir(parents=True, exist_ok=True)
        if not demucs_available():
            raise AudioSeparationError(
                "Demucs is not installed in this container.",
                code=CODE_PROVIDER_UNAVAILABLE,
                stage="provider",
            )

        if debug:
            debug.event(
                "provider",
                "Demucs two-stem separation starting",
                provider=self.name,
                model=self.model,
                device=self.device,
                output_dir=str(dest),
                input_path=str(source),
            )
        logger.info(
            "audio_separation_provider_started provider=%s model=%s input=%s",
            self.name,
            self.model,
            source,
        )

        raw_root = dest / "_demucs"
        raw_root.mkdir(parents=True, exist_ok=True)
        command = build_demucs_command(source, raw_root, model=self.model, device=self.device)
        env = os.environ.copy()
        env["TORCH_HOME"] = str(demucs_cache_dir())
        env["OMP_NUM_THREADS"] = str(_cpu_threads())
        expected = [
            dest / "dialogue.wav",
            dest / "background.wav",
        ]
        result = await run_logged_process(
            command,
            debug=debug,
            stage="provider",
            timeout_s=SEPARATION_TIMEOUT_S,
            is_cancelled=is_cancelled,
            output_paths=expected,
            env=env,
            term_grace_s=15.0,
        )
        if result.outcome is ProcessOutcome.CANCELLED:
            _cleanup_tree(raw_root, debug)
            raise AudioSeparationError(
                "Audio separation cancelled.",
                code=CODE_CANCELLED,
                stage="provider",
            )
        if result.outcome is ProcessOutcome.TIMEOUT:
            _cleanup_tree(raw_root, debug)
            raise AudioSeparationError(
                "Demucs timed out.",
                code=CODE_SEPARATION_FAILED,
                stage="provider",
            )
        if not result.ok:
            _cleanup_tree(raw_root, debug)
            if "No module named 'demucs'" in result.stderr_text or "is not installed" in str(result.stderr_text):
                raise AudioSeparationError(
                    "Demucs is not installed in this container.",
                    code=CODE_PROVIDER_UNAVAILABLE,
                    stage="provider",
                )
            detail = _failure_detail(result)
            message = "Demucs separation process failed."
            if detail:
                message = f"{message} {detail}"
            raise AudioSeparationError(
                message,
                code=CODE_SEPARATION_FAILED,
                stage="provider",
            )

        vocals = _stem_path(raw_root, self.model, source.stem, VOCALS_STEM)
        accompaniment = _stem_path(raw_root, self.model, source.stem, ACCOMPANIMENT_STEM)
        if vocals is None:
            _cleanup_tree(raw_root, debug)
            raise AudioSeparationError(
                "Demucs did not produce a dialogue/vocals stem.",
                code=CODE_MISSING_DIALOGUE,
                stage="provider",
            )
        if accompaniment is None:
            _cleanup_tree(raw_root, debug)
            raise AudioSeparationError(
                "Demucs did not produce a background/accompaniment stem.",
                code=CODE_MISSING_BACKGROUND,
                stage="provider",
            )

        dialogue_path = _copy_stem(vocals, dest / "dialogue.wav")
        background_path = _copy_stem(accompaniment, dest / "background.wav")
        _cleanup_tree(raw_root, debug)
        if debug:
            debug.event(
                "provider",
                "Demucs stems mapped to dialogue and background artifacts",
                vocals=str(vocals),
                accompaniment=str(accompaniment),
                dialogue=str(dialogue_path),
                background=str(background_path),
            )
        return SeparationResult(
            dialogue=_artifact(dialogue_path, role=ROLE_DIALOGUE, model=self.model, source=source),
            background=_artifact(background_path, role=ROLE_BACKGROUND, model=self.model, source=source),
            provider=self.name,
            model=self.model,
            metadata={"device": self.device, "mode": "two-stems=vocals"},
        )


def _cleanup_tree(path: Path, debug: DebugTrace | None) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        if debug:
            debug.event("cleanup", "Removed Demucs intermediate directory", path=str(path))
    except OSError:
        logger.warning("Could not remove Demucs work directory %s", path)
