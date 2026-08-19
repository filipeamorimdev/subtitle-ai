"""Append-only JSONL logger for non-OpenRouter job steps.

Translation jobs keep their detailed OpenRouter exchange log under the
OpenRouter-specific filename. For other job kinds (e.g. dub) we still want
to show a step-by-step timeline on the job detail page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.timefmt import utcnow_formatted


def job_event_log_path(config_dir: Path, job_id: int) -> Path:
    return config_dir / "logs" / "jobs" / f"job-{job_id}.jsonl"


class JobEventLog:
    """Write append-only JSONL records for a single job run."""

    def __init__(self, path: Path, *, job_id: int) -> None:
        self.path = path
        self.job_id = job_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Overwrite at job start (job_id corresponds to one execution row).
        self.path.write_text("", encoding="utf-8")

    def record(self, *, event: str, **fields: Any) -> None:
        payload = {
            "ts": utcnow_formatted(),
            "job_id": self.job_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

