"""Structured logging without credential-bearing context."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(level: str) -> None:
    """Configure JSON logs suitable for container aggregation."""

    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    """Return the application logger with no secret context attached."""

    return cast(structlog.stdlib.BoundLogger, structlog.get_logger("icloud_caldav_mcp"))
