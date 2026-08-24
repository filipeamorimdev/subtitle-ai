"""Reusable incremental debug traces for isolated pipeline features."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import AppConfig, get_app_config
from app.core.timefmt import DATETIME_FORMAT, utcnow_formatted
from app.media.process_runner import ProcessOutcome, ProcessResult, run_process

_SECRET_FRAGMENTS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "bearer",
    "credential",
)
_STDERR_LIMIT = 2000


def debug_trace_path(config: AppConfig, feature: str, task_id: str) -> Path:
    return config.config_dir / "debug" / feature / task_id / "trace.log"


def open_debug_trace(
    *,
    feature: str,
    task_id: str,
    enabled: bool,
    config: AppConfig | None = None,
) -> DebugTrace | None:
    if not enabled:
        return None
    cfg = config or get_app_config()
    return DebugTrace(debug_trace_path(cfg, feature, task_id), feature=feature, task_id=task_id)


def redact_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for part in command:
        text = str(part)
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        lower = text.lower()
        if any(fragment in lower for fragment in _SECRET_FRAGMENTS):
            if "=" in text:
                key, _, _rest = text.partition("=")
                redacted.append(f"{key}=***")
            else:
                redacted.append(text)
                hide_next = True
            continue
        redacted.append(text)
    return redacted


def redact_data(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
            clean[key] = "***"
        elif isinstance(value, dict):
            clean[key] = redact_data(value)
        else:
            clean[key] = value
    return clean


def truncate_text(text: str, limit: int = _STDERR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _format_value(value: Any) -> str:
    if value is None or isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


class DebugTrace:
    """Append-only, crash-safe trace file for one task run."""

    def __init__(self, path: Path, *, feature: str, task_id: str) -> None:
        self.path = Path(path)
        self.feature = feature
        self.task_id = task_id
        self.last_stage = "start"
        self._finished = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        started = utcnow_formatted()
        header = (
            f"# Subtitle AI debug trace\n"
            f"feature={feature}\n"
            f"task_id={task_id}\n"
            f"started={started}\n"
            f"path={self.path}\n"
            f"\n"
        )
        self.path.write_text(header, encoding="utf-8")
        self._append("EVENT", "task started", stage="task", started=started)

    @property
    def finished(self) -> bool:
        return self._finished

    def event(self, stage: str, message: str, **data: Any) -> None:
        data.pop("stage", None)
        self.last_stage = stage
        self._append("EVENT", message, stage=stage, **data)

    def decision(self, message: str, **data: Any) -> None:
        self._append("DECISION", message, stage=data.pop("stage", "decision"), **data)

    def command(self, command: Sequence[str], **data: Any) -> None:
        payload = dict(data)
        payload["command"] = redact_command(command)
        self._append("COMMAND", " ".join(redact_command(command)), **payload)

    def finish(self, status: str, **data: Any) -> None:
        if self._finished:
            return
        self._finished = True
        ended = utcnow_formatted()
        data.pop("stage", None)
        data.pop("status", None)
        self._append("FINISH", f"status={status}", stage="result", status=status, ended=ended, **data)

    def _append(self, kind: str, message: str, **data: Any) -> None:
        ts = datetime.now(timezone.utc).strftime(DATETIME_FORMAT)
        lines = [f"{ts}  {kind:<8}  {message}"]
        payload = redact_data({k: v for k, v in data.items() if v is not None})
        for key, value in payload.items():
            if key == "command" and isinstance(value, list):
                lines.append(f"  command={' '.join(str(part) for part in value)}")
                continue
            lines.append(f"  {key}={_format_value(value)}")
        block = "\n".join(lines) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(block)
            handle.flush()


async def run_logged_process(
    argv: Sequence[str],
    *,
    debug: DebugTrace | None = None,
    stage: str,
    timeout_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    output_paths: Sequence[str | Path] | None = None,
    env: dict[str, str] | None = None,
    term_grace_s: float = 2.0,
) -> ProcessResult:
    """Run a subprocess and record command timing when a debug trace is active."""
    command = [str(part) for part in argv]
    started_mono = time.monotonic()
    start_ts = utcnow_formatted()
    if debug:
        debug.event(stage, "command starting", command=redact_command(command))
    result = await run_process(
        command,
        timeout_s=timeout_s,
        is_cancelled=is_cancelled,
        output_paths=output_paths,
        env=env,
        term_grace_s=term_grace_s,
    )
    elapsed_s = round(time.monotonic() - started_mono, 3)
    payload: dict[str, Any] = {
        "stage": stage,
        "start": start_ts,
        "end": utcnow_formatted(),
        "duration_s": elapsed_s,
        "exit_code": result.returncode,
        "outcome": result.outcome.value,
        "timeout": result.outcome is ProcessOutcome.TIMEOUT,
        "cancelled": result.outcome is ProcessOutcome.CANCELLED,
    }
    if not result.ok:
        stderr = truncate_text(result.stderr_text)
        if stderr:
            payload["stderr"] = stderr
    if debug:
        debug.command(command, **payload)
    return result
