"""Structured application logging."""

from __future__ import annotations

import logging
import sys
from typing import Any


class ComponentFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "component"):
            record.component = record.name
        if not hasattr(record, "job_id"):
            record.job_id = "-"
        return super().format(record)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ComponentFormatter(
            fmt="%(asctime)s level=%(levelname)s component=%(component)s job_id=%(job_id)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)


def get_logger(component: str) -> logging.LoggerAdapter[Any]:
    logger = logging.getLogger(component)
    return logging.LoggerAdapter(logger, {"component": component, "job_id": "-"})
