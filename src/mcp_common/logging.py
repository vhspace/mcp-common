"""Structured logging setup for MCP servers."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class JSONFormatter(logging.Formatter):
    """JSON log formatter for container/production environments."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    name: str | None = None,
) -> logging.Logger:
    """Configure logging for an MCP server.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: Use JSON formatting (recommended for containers).
        name: Logger name. Defaults to root logger.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    normalized = level.upper()
    if normalized not in _VALID_LEVELS:
        normalized = "INFO"
    logger.setLevel(getattr(logging, normalized))

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
        handler.setFormatter(logging.Formatter(fmt))

    logger.addHandler(handler)
    return logger
