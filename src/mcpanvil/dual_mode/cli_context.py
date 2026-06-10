"""``CliContext`` — minimal stand-in for ``fastmcp.Context`` for CLI runs.

FastMCP tools may declare a ``ctx: fastmcp.Context`` parameter to emit
progress, structured log events, or fetch session state. When the same
tool runs from a Typer command, no MCP session exists, so the CLI
side needs a shim that maps the Context API to terminal output / standard
loggers.

The shim is deliberately small: it covers the async methods an MCP server
typically calls (``info`` / ``warning`` / ``error`` /
``debug`` / ``log`` / ``report_progress``). Other Context methods are
not stubbed; calling them on a :class:`CliContext` raises
``AttributeError`` so missing coverage is discoverable rather than
silently no-op'd.

Drift detection (warning when the installed ``fastmcp`` exposes async
Context methods this shim does not cover) is **opt-in** and silent by
default: set ``MCPANVIL_WARN_CONTEXT_DRIFT=1`` to surface the warning.
The proactive warning was noise on every package import — every pytest
run, CLI invocation, and conformance CI step across downstream MCPs — so
it is now off unless explicitly enabled. The runtime failure mode is
unchanged: calling an unshimmed Context method on a :class:`CliContext`
still raises ``AttributeError`` loudly.
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from collections.abc import Mapping
from typing import Any

__all__ = ["CliContext"]


_DEFAULT_LOGGER_NAME = "mcpanvil.dual_mode.cli_context"


class CliContext:
    """Minimal Context stand-in for CLI-driven tool invocation.

    Methods mirror the ``fastmcp.Context`` API an MCP server typically relies on:

    * :meth:`info` / :meth:`warning` / :meth:`error` / :meth:`debug` /
      :meth:`log` — route to the standard library ``logging`` module at
      the matching level, preserving ``logger_name`` and ``extra``.
    * :meth:`report_progress` — emit a single ``"[NN%] <message>"`` line
      to stderr so long-running CLI commands stay observable. Falls back
      to ``"<progress>/<total>"`` when ``total`` is unknown.

    All methods are ``async`` because FastMCP's Context API is, and the
    builder needs to ``await`` them uniformly whether the tool itself is
    sync or async.

    Any other ``fastmcp.Context`` method is intentionally not stubbed:
    calling it on a :class:`CliContext` raises ``AttributeError`` so the
    gap is discoverable rather than silently no-op'd. To proactively audit
    which Context methods this shim does not cover, set
    ``MCPANVIL_WARN_CONTEXT_DRIFT=1`` — drift detection is off by default
    to avoid warning noise on every import.

    Args:
        logger: Optional logger; defaults to
            ``logging.getLogger("mcpanvil.dual_mode.cli_context")``.
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

_DRIFT_ENV_VAR = "MCPANVIL_WARN_CONTEXT_DRIFT"
"""Env var that opts in to the proactive Context-drift warning.

Set to a truthy value (``"1"``, ``"true"``, ``"yes"``, ``"on"``) to make
:func:`_detect_context_drift` emit its warning. Unset (the default) keeps
it silent so the warning does not pollute pytest output, CLI runs, or the
conformance CI step across downstream MCPs.
"""

_drift_warned_once = False
"""Module-level flag so :func:`_detect_context_drift` fires at most once.

Even when drift detection is opted in via :data:`_DRIFT_ENV_VAR`, the
warning should surface only once per process. Without this flag, every
code path that re-runs the detector — and the import-time call below —
would emit a duplicate warning.
"""


def _env_truthy(value: str | None) -> bool:
    """Return ``True`` for common truthy env-var spellings.

    Matches the convention used across MCP servers built on mcpanvil:
    a value is truthy when it equals
    ``"1"``, ``"true"``, ``"yes"``, or ``"on"`` (case-insensitive, trimmed).
    """
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _detect_context_drift(*, force: bool = False) -> None:
    """Warn if FastMCP's Context exposes async methods CliContext omits.

    Opt-in and silent by default. The warning is emitted only when either
    ``force=True`` is passed (the test escape hatch) or the
    :data:`_DRIFT_ENV_VAR` (``MCPANVIL_WARN_CONTEXT_DRIFT``) env var is
    set to a truthy value. When enabled, it still fires at most once per
    process unless ``force=True`` is passed.

    Best-effort: import failures or signature inspection failures are
    swallowed silently so this never breaks an application that just wants
    the shim. This is purely a proactive heads-up — the real failure mode
    (calling an unshimmed Context method on a :class:`CliContext`) still
    raises ``AttributeError`` loudly and is unaffected by this gate.
    """
    global _drift_warned_once
    if not force:
        if not _env_truthy(os.environ.get(_DRIFT_ENV_VAR)):
            return
        if _drift_warned_once:
            return

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
            "mcpanvil.dual_mode.CliContext does not shim these async "
            "fastmcp.Context methods: "
            + ", ".join(sorted(missing))
            + ". Calling them on a CliContext will raise AttributeError; "
            "tools that need them must be marked mcp_only=True or guard "
            "their Context usage.",
            stacklevel=2,
        )
    _drift_warned_once = True


_detect_context_drift()
