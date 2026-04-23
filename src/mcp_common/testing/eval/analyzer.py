"""Placeholder for ``.eval`` log analysis.

Reads Inspect AI ``.eval`` log files and extracts structured failure
information that can be fed to the issue filer or surfaced in CI summaries.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class EvalFailure(BaseModel):
    """A single failure extracted from an ``.eval`` log."""

    server: str
    """MCP server name that was under evaluation."""

    scenario: str
    """Scenario identifier or prompt text."""

    tool_calls: list[str] = []
    """Ordered list of tool names the agent called."""

    error: str = ""
    """Error message or mismatch description."""

    score: float = 0.0
    """Numeric score assigned by the scorer (0-1)."""

    trace_excerpt: str = ""
    """Relevant excerpt from the execution trace."""


def analyze_eval_log(log_path: str | Path) -> list[EvalFailure]:
    """Read an Inspect AI ``.eval`` log and extract failures.

    Parses the structured log, identifies scenarios that scored below the
    passing threshold, and returns a list of :class:`EvalFailure` records
    with enough context for triage.

    Args:
        log_path: Path to an ``.eval`` log file produced by ``inspect eval``.

    Returns:
        A list of :class:`EvalFailure` for every below-threshold scenario.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #47.
    """
    raise NotImplementedError("See issue #47")
