"""Authoritative localization-task progress checkpoints.

Stored on LocalizationTaskRow.metadata_json["checkpoints"].
States: pending | active | done | failed | skipped.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

CHECKPOINT_IDS: tuple[str, ...] = (
    "source",
    "extract",
    "translate",
    "validate",
    "write",
    "sync",
    "verify",
)

CHECKPOINT_LABELS: dict[str, str] = {
    "source": "Source found",
    "extract": "Source extracted",
    "translate": "Translating",
    "validate": "Validating",
    "write": "Writing subtitle",
    "sync": "Bazarr sync",
    "verify": "Verification",
}

CHECKPOINT_STATES = frozenset({"pending", "active", "done", "failed", "skipped"})


def default_checkpoints() -> dict[str, str]:
    return {cid: "pending" for cid in CHECKPOINT_IDS}


def read_checkpoints(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    raw = (metadata or {}).get("checkpoints") if metadata else None
    out = default_checkpoints()
    if isinstance(raw, dict):
        for cid in CHECKPOINT_IDS:
            state = raw.get(cid)
            if isinstance(state, str) and state in CHECKPOINT_STATES:
                out[cid] = state
    return out


def merge_checkpoints(
    metadata: dict[str, Any] | None,
    updates: Mapping[str, str],
) -> dict[str, Any]:
    meta = dict(metadata or {})
    current = read_checkpoints(meta)
    for cid, state in updates.items():
        if cid not in CHECKPOINT_IDS:
            continue
        if state not in CHECKPOINT_STATES:
            continue
        current[cid] = state
    meta["checkpoints"] = current
    return meta


def progress_steps(checkpoints: Mapping[str, str]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for cid in CHECKPOINT_IDS:
        state = checkpoints.get(cid, "pending")
        steps.append({"id": cid, "label": CHECKPOINT_LABELS[cid], "state": state})
    return steps


def has_checkpoint_data(metadata: Mapping[str, Any] | None) -> bool:
    raw = (metadata or {}).get("checkpoints") if metadata else None
    return isinstance(raw, dict) and any(raw.get(cid) in CHECKPOINT_STATES for cid in CHECKPOINT_IDS)


def mark_pipeline_ready_for_translate(*, extracted: bool) -> dict[str, str]:
    """Source is present; extract is done or skipped before translation."""
    return {
        "source": "done",
        "extract": "done" if extracted else "skipped",
        "translate": "active",
        "validate": "pending",
        "write": "pending",
        "sync": "pending",
        "verify": "pending",
    }


def mark_existing_target_complete() -> dict[str, str]:
    """Target already present — no AI work; verification succeeded."""
    return {
        "source": "skipped",
        "extract": "skipped",
        "translate": "skipped",
        "validate": "skipped",
        "write": "skipped",
        "sync": "done",
        "verify": "done",
    }


def mark_write_complete() -> dict[str, str]:
    """Translation/validation/write succeeded; Bazarr sync/verify in progress."""
    return {
        "translate": "done",
        "validate": "done",
        "write": "done",
        "sync": "active",
        "verify": "active",
    }
