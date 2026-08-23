"""Compare TTS duration to the available timeline slot."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger("dubbing.timing")

DEFAULT_MAX_SPEED = 1.2
DEFAULT_MIN_SPEED = 0.85
ADAPT_RATIO = 1.2


@dataclass(frozen=True)
class TimingDecision:
    action: str  # exact | silence | speed | adapt
    speed: float
    available: float
    actual: float
    reason: str

    def to_dict(self) -> dict[str, float | str]:
        return {
            "action": self.action,
            "speed": self.speed,
            "available": self.available,
            "actual": self.actual,
            "reason": self.reason,
        }


class TimingEngine:
    def __init__(self, *, max_speed: float = DEFAULT_MAX_SPEED, min_speed: float = DEFAULT_MIN_SPEED) -> None:
        self.max_speed = max_speed
        self.min_speed = min_speed

    def decide(self, *, actual: float, available: float) -> TimingDecision:
        actual = max(0.0, actual)
        available = max(0.001, available)
        if abs(actual - available) <= 0.05:
            return TimingDecision("exact", 1.0, available, actual, "TTS duration matches the slot")
        if actual <= available:
            return TimingDecision(
                "silence",
                1.0,
                available,
                actual,
                "TTS is shorter than the slot; keep natural silence",
            )
        ratio = actual / available
        if ratio <= self.max_speed:
            return TimingDecision(
                "speed",
                min(ratio, self.max_speed),
                available,
                actual,
                f"Limited speed-up ({ratio:.2f}x) within {self.max_speed:.2f}x",
            )
        return TimingDecision(
            "adapt",
            self.max_speed,
            available,
            actual,
            f"TTS is {ratio:.2f}x the slot; adapt dialogue instead of overspeeding",
        )
