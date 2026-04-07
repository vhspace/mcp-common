"""Structured logging setup for MCP servers."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ssl_warnings_suppressed = False


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


def suppress_ssl_warnings() -> None:
    """Suppress urllib3 ``InsecureRequestWarning`` globally.

    Call at startup when SSL verification is intentionally disabled (e.g.
    internal BMCs, self-signed certs).  Many MCP servers talk to internal
    services with ``verify=False`` and these warnings clutter output and
    get swallowed by agents.

    Safe to call multiple times — only the first call has any effect.
    No-op if urllib3 is not installed.
    """
    global _ssl_warnings_suppressed
    if _ssl_warnings_suppressed:
        return
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass
    _ssl_warnings_suppressed = True


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    name: str | None = None,
    suppress_ssl: bool = True,
) -> logging.Logger:
    """Configure logging for an MCP server.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: Use JSON formatting (recommended for containers).
        name: Logger name. Defaults to root logger.
        suppress_ssl: Suppress urllib3 InsecureRequestWarning. Defaults to
            ``True`` because MCP servers commonly talk to internal services
            with self-signed certificates.

    Returns:
        Configured logger instance.
    """
    if suppress_ssl:
        suppress_ssl_warnings()

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
