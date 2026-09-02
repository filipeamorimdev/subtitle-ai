"""Generate fixed pt-PT audition clips from a character reference."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_app_config
from app.localization.dubbing.providers.chatterbox import (
    ChatterboxVoiceProfile,
    TTSError,
    load_chatterbox_model,
    resolve_voice_profile,
    wav_duration_seconds,
    write_chatterbox_wav,
)
from app.localization.dubbing.voice_library.paths import resolve_reference_path

AUDITION_LINES_PT_PT: tuple[tuple[str, str], ...] = (
    ("neutral", "Olá! Hoje vamos resolver este problema juntos."),
    ("question", "Sabes onde ficou a mochila azul?"),
    ("rapid", "Depressa, temos de chegar à estação antes que o comboio parta!"),
    ("quiet", "Fica quieto... alguém pode ouvir-nos."),
    ("excited", "Conseguimos! Foi a melhor aventura de sempre!"),
)

AUDITION_CFG_WEIGHTS: tuple[float, ...] = (0.5, 0.35, 0.0)


@dataclass(frozen=True)
class AuditionCandidate:
    line_id: str
    cfg_weight: float
    exaggeration: float
    seed: int
    wav_path: str
    duration: float | None
    profile_id: str


@dataclass(frozen=True)
class AuditionMatrix:
    reference_sha256: str
    target_language: str
    candidates: list[AuditionCandidate]


class VoiceAuditionService:
    """Render a small deterministic audition matrix for human approval."""

    def __init__(self, *, target_language: str = "pt-PT") -> None:
        self.target_language = target_language

    async def render_matrix(
        self,
        *,
        reference_relative_path: str,
        voice_model: str | None = None,
        seeds: tuple[int, ...] = (0, 1, 2),
    ) -> AuditionMatrix:
        reference = resolve_reference_path(reference_relative_path)
        if not reference.is_file():
            raise TTSError(f"Reference audio is missing: {reference.name}")
        digest = hashlib.sha256(reference.read_bytes()).hexdigest()
        profile = resolve_voice_profile(voice_model, self.target_language)
        output_root = (
            get_app_config().config_dir
            / "cache"
            / "voice-auditions"
            / digest[:16]
        )
        output_root.mkdir(parents=True, exist_ok=True)

        model = await asyncio.to_thread(
            load_chatterbox_model,
            target_language=self.target_language,
        )
        candidates: list[AuditionCandidate] = []
        for line_id, text in AUDITION_LINES_PT_PT:
            for cfg_weight in AUDITION_CFG_WEIGHTS:
                for seed in seeds:
                    out = output_root / f"{line_id}-cfg{cfg_weight:.2f}-seed{seed}.wav"
                    await asyncio.to_thread(
                        _synthesize_audition_clip,
                        model,
                        profile,
                        text,
                        reference,
                        out,
                        cfg_weight=cfg_weight,
                        seed=seed,
                    )
                    candidates.append(
                        AuditionCandidate(
                            line_id=line_id,
                            cfg_weight=cfg_weight,
                            exaggeration=profile.exaggeration,
                            seed=seed,
                            wav_path=str(out),
                            duration=wav_duration_seconds(out),
                            profile_id=profile.id,
                        )
                    )
        return AuditionMatrix(
            reference_sha256=digest,
            target_language=self.target_language,
            candidates=candidates,
        )

    def matrix_to_json(self, matrix: AuditionMatrix) -> dict[str, Any]:
        return {
            "reference_sha256": matrix.reference_sha256,
            "target_language": matrix.target_language,
            "candidates": [
                {
                    "line_id": item.line_id,
                    "cfg_weight": item.cfg_weight,
                    "exaggeration": item.exaggeration,
                    "seed": item.seed,
                    "wav_path": item.wav_path,
                    "duration": item.duration,
                    "profile_id": item.profile_id,
                }
                for item in matrix.candidates
            ],
        }


def _synthesize_audition_clip(
    model: Any,
    profile: ChatterboxVoiceProfile,
    text: str,
    reference: Path,
    output: Path,
    *,
    cfg_weight: float,
    seed: int,
) -> None:
    del seed  # Chatterbox seed control is not exposed in the pinned API yet.
    write_chatterbox_wav(
        model,
        profile,
        text,
        output,
        audio_prompt_path=reference,
        cfg_weight=cfg_weight,
    )
