"""Reusable async subprocess runner with cancellation, timeout, and cleanup."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("process_runner")

CancelCheck = Callable[[], bool]


class ProcessOutcome(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ProcessError(Exception):
    def __init__(
        self,
        message: str,
        *,
        outcome: ProcessOutcome,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class ProcessResult:
    outcome: ProcessOutcome
    returncode: int | None
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.outcome is ProcessOutcome.COMPLETED and self.returncode == 0

    @property
    def stderr_text(self) -> str:
        return (self.stderr or b"").decode("utf-8", errors="replace")

    @property
    def stdout_text(self) -> str:
        return (self.stdout or b"").decode("utf-8", errors="replace")


async def _stop_process(proc: asyncio.subprocess.Process, *, grace_s: float) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=max(0.05, grace_s))
        return
    except TimeoutError:
        pass
    except ProcessLookupError:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (TimeoutError, ProcessLookupError):
        pass


def _unlink_partial(paths: Sequence[str | Path]) -> None:
    for raw in paths:
        path = Path(raw)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.warning("Could not remove partial output %s", path)


async def run_process(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout_s: float | None = None,
    is_cancelled: CancelCheck | None = None,
    cancel_poll_s: float = 0.25,
    term_grace_s: float = 2.0,
    output_paths: Sequence[str | Path] | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run argv and wait concurrently for completion, cancel, or timeout.

    On cancel/timeout the process receives SIGTERM, then SIGKILL after
    ``term_grace_s``. Partial ``output_paths`` are deleted unless the command
    completed successfully.
    """
    command = [str(part) for part in argv]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise ProcessError(
            f"{command[0]} is not installed",
            outcome=ProcessOutcome.FAILED,
        ) from exc

    communicate = asyncio.create_task(
        proc.communicate(input=input_bytes) if input_bytes is not None else proc.communicate()
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s if timeout_s is not None else None
    outcome: ProcessOutcome | None = None

    try:
        while True:
            if is_cancelled and is_cancelled():
                outcome = ProcessOutcome.CANCELLED
                break
            wait_s = cancel_poll_s
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    outcome = ProcessOutcome.TIMEOUT
                    break
                wait_s = min(wait_s, remaining)
            done, _pending = await asyncio.wait({communicate}, timeout=wait_s)
            if communicate in done:
                stdout, stderr = communicate.result()
                code = proc.returncode
                if code == 0:
                    return ProcessResult(ProcessOutcome.COMPLETED, code, stdout or b"", stderr or b"")
                result = ProcessResult(ProcessOutcome.FAILED, code, stdout or b"", stderr or b"")
                _unlink_partial(output_paths or ())
                return result

        await _stop_process(proc, grace_s=term_grace_s)
        if not communicate.done():
            communicate.cancel()
            try:
                await communicate
            except (asyncio.CancelledError, Exception):
                pass
        _unlink_partial(output_paths or ())
        stdout = b""
        stderr = b""
        if communicate.done() and not communicate.cancelled():
            try:
                stdout, stderr = communicate.result()
            except Exception:
                stdout, stderr = b"", b""
        return ProcessResult(outcome or ProcessOutcome.FAILED, proc.returncode, stdout or b"", stderr or b"")
    except asyncio.CancelledError:
        await _stop_process(proc, grace_s=term_grace_s)
        if not communicate.done():
            communicate.cancel()
            try:
                await communicate
            except (asyncio.CancelledError, Exception):
                pass
        _unlink_partial(output_paths or ())
        raise


async def run_process_checked(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    input_text: str | None = None,
    timeout_s: float | None = None,
    is_cancelled: CancelCheck | None = None,
    output_paths: Sequence[str | Path] | None = None,
    term_grace_s: float = 2.0,
) -> ProcessResult:
    """Like ``run_process`` but raises ``ProcessError`` unless completed successfully."""
    payload = input_bytes
    if payload is None and input_text is not None:
        payload = input_text.encode("utf-8")
    result = await run_process(
        argv,
        input_bytes=payload,
        timeout_s=timeout_s,
        is_cancelled=is_cancelled,
        output_paths=output_paths,
        term_grace_s=term_grace_s,
    )
    if result.outcome is ProcessOutcome.CANCELLED:
        raise ProcessError("Process cancelled", outcome=result.outcome, returncode=result.returncode)
    if result.outcome is ProcessOutcome.TIMEOUT:
        raise ProcessError(
            f"Command timed out: {' '.join(str(part) for part in argv[:2])}...",
            outcome=result.outcome,
            returncode=result.returncode,
            stderr=result.stderr_text[-400:],
        )
    if not result.ok:
        detail = result.stderr_text[-400:] or "unknown error"
        raise ProcessError(
            f"Command failed exit={result.returncode} stderr={detail}",
            outcome=ProcessOutcome.FAILED,
            returncode=result.returncode,
            stderr=detail,
        )
    return result
