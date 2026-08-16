"""Localization task status transitions."""

from __future__ import annotations

from typing import Final

ACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "requested",
        "planning",
        "waiting_for_source",
        "processing",
        "verifying",
    }
)

TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "completed",
        "failed",
        "blocked",
        "cancelled",
    }
)

# from_status -> allowed next statuses
ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "requested": frozenset(
        {"planning", "waiting_for_source", "processing", "verifying", "completed", "cancelled", "blocked"}
    ),
    "planning": frozenset(
        {
            "waiting_for_source",
            "processing",
            "verifying",
            "completed",
            "blocked",
            "failed",
            "cancelled",
        }
    ),
    "waiting_for_source": frozenset(
        {"planning", "processing", "completed", "failed", "cancelled", "blocked"}
    ),
    "processing": frozenset({"verifying", "waiting_for_source", "failed", "cancelled", "completed"}),
    "verifying": frozenset({"completed", "failed", "cancelled", "processing"}),
    "completed": frozenset(),
    "failed": frozenset({"planning", "requested"}),  # retry re-enters planning
    "blocked": frozenset({"planning", "requested"}),
    "cancelled": frozenset(),
}


class InvalidTaskTransition(ValueError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Invalid localization task transition: {current} → {target}")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    return target in allowed


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTaskTransition(current, target)
