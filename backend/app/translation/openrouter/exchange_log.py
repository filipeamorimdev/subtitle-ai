"""Per-job OpenRouter request/response exchange logging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.core.timefmt import utcnow_formatted


def utcnow_iso() -> str:
    """Return current UTC time as Y-m-d H:i:s (name kept for call sites)."""
    return utcnow_formatted()


def job_openrouter_log_path(config_dir: Path, job_id: int) -> Path:
    return config_dir / "logs" / "jobs" / f"job-{job_id}-openrouter.jsonl"


class ExchangeRecorder(Protocol):
    def record(self, record: dict[str, Any]) -> None: ...


class JobOpenRouterExchangeLog:
    """Append-only JSONL log of OpenRouter exchanges for one translation job."""

    def __init__(self, path: Path, *, job_id: int) -> None:
        self.path = path
        self.job_id = job_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One file per job run; overwrite if the same job id is retried as a new attempt.
        self.path.write_text("", encoding="utf-8")

    def record(self, record: dict[str, Any]) -> None:
        payload = {"ts": utcnow_iso(), "job_id": self.job_id, **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
