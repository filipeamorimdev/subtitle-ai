"""Localization tasks: media-centric goals over low-level jobs."""

from typing import Any

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "ActiveTaskExistsError",
    "BazarrVerificationService",
    "LocalizationPipeline",
    "LocalizationTaskService",
    "TaskPlanner",
    "UnsupportedCapabilityError",
    "VerificationResult",
]


def __getattr__(name: str) -> Any:
    if name in {"ACTIVE_STATUSES", "TERMINAL_STATUSES"}:
        from app.localization.state import ACTIVE_STATUSES, TERMINAL_STATUSES

        return ACTIVE_STATUSES if name == "ACTIVE_STATUSES" else TERMINAL_STATUSES
    if name in {"ActiveTaskExistsError", "LocalizationTaskService", "UnsupportedCapabilityError"}:
        from app.localization.service import (
            ActiveTaskExistsError,
            LocalizationTaskService,
            UnsupportedCapabilityError,
        )

        return {
            "ActiveTaskExistsError": ActiveTaskExistsError,
            "LocalizationTaskService": LocalizationTaskService,
            "UnsupportedCapabilityError": UnsupportedCapabilityError,
        }[name]
    if name == "TaskPlanner":
        from app.localization.planner import TaskPlanner

        return TaskPlanner
    if name == "LocalizationPipeline":
        from app.localization.pipeline import LocalizationPipeline

        return LocalizationPipeline
    if name in {"BazarrVerificationService", "VerificationResult"}:
        from app.localization.verification import BazarrVerificationService, VerificationResult

        return BazarrVerificationService if name == "BazarrVerificationService" else VerificationResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
