"""``CliContext`` — minimal stand-in for ``fastmcp.Context`` for CLI runs.

FastMCP tools may declare a ``ctx: fastmcp.Context`` parameter to emit
progress, structured log events, or fetch session state. When the same
tool runs from a Typer command, no MCP session exists, so the CLI
side needs a shim that maps the Context API to terminal output / standard
loggers.

The shim is deliberately small: it covers the async methods every
vhspace MCP actually calls (``info`` / ``warning`` / ``error`` /
``debug`` / ``log`` / ``report_progress``). Other Context methods are
not stubbed; calling them on a :class:`CliContext` raises
``AttributeError`` so missing coverage is discoverable rather than
silently no-op'd.

A module-import-time warning is emitted if the installed ``fastmcp``
exposes async Context methods this shim does not cover, so drift is
visible without a runtime crash.
"""

from __future__ import annotations

import logging
import sys
import warnings
from collections.abc import Mapping
from typing import Any

__all__ = ["CliContext"]


_DEFAULT_LOGGER_NAME = "mcp_common.dual_mode.cli_context"


class CliContext:
    """Minimal Context stand-in for CLI-driven tool invocation.

    Methods mirror the ``fastmcp.Context`` API every vhspace MCP relies on:

    * :meth:`info` / :meth:`warning` / :meth:`error` / :meth:`debug` /
      :meth:`log` — route to the standard library ``logging`` module at
      the matching level, preserving ``logger_name`` and ``extra``.
    * :meth:`report_progress` — emit a single ``"[NN%] <message>"`` line
      to stderr so long-running CLI commands stay observable. Falls back
      to ``"<progress>/<total>"`` when ``total`` is unknown.

    All methods are ``async`` because FastMCP's Context API is, and the
    builder needs to ``await`` them uniformly whether the tool itself is
    sync or async.

    Args:
        logger: Optional logger; defaults to
            ``logging.getLogger("mcp_common.dual_mode.cli_context")``.
        stream: Output stream for :meth:`report_progress`. Defaults to
            ``sys.stderr`` so stdout stays clean for JSON payloads.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        stream: Any | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(_DEFAULT_LOGGER_NAME)
        self._stream = stream if stream is not None else sys.stderr

    async def info(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Log ``message`` at INFO. Mirrors ``Context.info``."""
        self._log(logging.INFO, message, logger_name, extra)

    async def warning(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Log ``message`` at WARNING. Mirrors ``Context.warning``."""
        self._log(logging.WARNING, message, logger_name, extra)

    async def error(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Log ``message`` at ERROR. Mirrors ``Context.error``."""
        self._log(logging.ERROR, message, logger_name, extra)

    async def debug(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Log ``message`` at DEBUG. Mirrors ``Context.debug``."""
        self._log(logging.DEBUG, message, logger_name, extra)

    async def log(
        self,
        message: str,
        level: str | None = None,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Log ``message`` at ``level`` (defaults to INFO).

        ``level`` is the MCP ``LoggingLevel`` enum value (``"debug"``,
        ``"info"``, ``"warning"``, ``"error"``, ``"critical"``,
        ``"notice"``, ``"alert"``, ``"emergency"``). Unknown levels fall
        back to ``INFO`` rather than raising, matching FastMCP's
        liberal interpretation.
        """
        py_level = _MCP_LOGGING_LEVELS.get((level or "info").lower(), logging.INFO)
        self._log(py_level, message, logger_name, extra)

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        """Emit a progress line to stderr (no-op in MCP terms).

        Format:
        * ``"[NN%] <message>"`` when ``total`` is known and non-zero.
        * ``"<progress>/<total> <message>"`` when ``total`` is known but
          zero (avoid divide-by-zero).
        * ``"<progress> <message>"`` when ``total`` is ``None``.

        ``message`` is omitted from the rendered line when ``None``.
        """
        line = self._format_progress(progress, total, message)
        try:
            print(line, file=self._stream, flush=True)
        except Exception:
            self._logger.debug("CliContext progress write failed: %s", line)

    def _format_progress(
        self,
        progress: float,
        total: float | None,
        message: str | None,
    ) -> str:
        if total is None:
            head = f"{progress:g}"
        elif total > 0:
            pct = max(0.0, min(100.0, (progress / total) * 100.0))
            head = f"[{pct:5.1f}%]"
        else:
            head = f"{progress:g}/{total:g}"
        if message:
            return f"{head} {message}"
        return head

    def _log(
        self,
        level: int,
        message: str,
        logger_name: str | None,
        extra: Mapping[str, Any] | None,
    ) -> None:
        target = logging.getLogger(logger_name) if logger_name else self._logger
        target.log(level, message, extra=dict(extra) if extra else None)


_MCP_LOGGING_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "alert": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


_SHIMMED_METHODS = frozenset({"info", "warning", "error", "debug", "log", "report_progress"})


def _detect_context_drift() -> None:
    """Warn if FastMCP's Context exposes async methods CliContext omits.

    Module-import-time best-effort: import failures or signature
    inspection failures are swallowed silently so this never breaks an
    application that just wants the shim.
    """
    try:
        import inspect

        from fastmcp import Context
    except Exception:
        return

    missing: list[str] = []
    for name, attr in inspect.getmembers(Context):
        if name.startswith("_"):
            continue
        if not inspect.iscoroutinefunction(attr):
            continue
        if name in _SHIMMED_METHODS:
            continue
        missing.append(name)

    if missing:
        warnings.warn(
            "mcp_common.dual_mode.CliContext does not shim these async "
            "fastmcp.Context methods: "
            + ", ".join(sorted(missing))
            + ". Calling them on a CliContext will raise AttributeError; "
            "tools that need them must be marked mcp_only=True or guard "
            "their Context usage.",
            stacklevel=2,
        )


_detect_context_drift()
