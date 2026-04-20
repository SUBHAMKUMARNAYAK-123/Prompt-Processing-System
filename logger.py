"""
Structured JSON logger using structlog.
All log entries include timestamp, level, logger name, and key=value pairs.
"""
from __future__ import annotations

import logging
import sys

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(sys.stdout),
)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
