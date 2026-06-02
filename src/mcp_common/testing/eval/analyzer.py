"""Read Inspect AI ``.eval`` log files and extract structured failure information.

Parses eval logs produced by ``inspect eval``, identifies samples that did not
score CORRECT, and returns :class:`EvalFailure` records with enough context for
triage or automatic issue filing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from inspect_ai.log import EvalLog, EvalSample, read_eval_log
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.scorer import CORRECT
from pydantic import BaseModel

_log = logging.getLogger(__name__)


class EvalFailure(BaseModel):
    """A single failure extracted from an ``.eval`` log."""

    server: str
    """MCP server name that was under evaluation."""

    scenario: str
    """Scenario identifier or prompt text."""

    tool_calls: list[str] = []
    """Ordered list of tool names the agent called."""

    error: str = ""
    """Error message or mismatch description from the scorer."""

    score: str = ""
    """Score value string assigned by the scorer (e.g. ``"I"`` for INCORRECT)."""

    trace_excerpt: str = ""
    """Relevant excerpt from the execution trace (last assistant messages, ~500 chars)."""


def _server_from_task_name(task: str) -> str:
    """Derive a server name from an Inspect AI task name.

    Convention: ``"netbox_mcp_eval"`` -> ``"netbox-mcp"``.
    Strips a trailing ``_eval`` suffix and converts underscores to hyphens.
    """
    name = re.sub(r"_eval$", "", task)
    return name.replace("_", "-")


def _extract_input_text(sample: EvalSample) -> str:
    """Get the human-readable input text from a sample."""
    if isinstance(sample.input, str):
        return sample.input
    for msg in sample.input:
        if isinstance(msg, ChatMessageUser):
            text = msg.text if hasattr(msg, "text") else str(msg.content)
            return text.strip()
    return str(sample.id)


def _extract_tool_call_names(sample: EvalSample) -> list[str]:
    """Pull tool function names from the sample message history."""
    names: list[str] = []
    for msg in sample.messages:
        if isinstance(msg, ChatMessageAssistant) and msg.tool_calls:
            for tc in msg.tool_calls:
                names.append(tc.function)
    return names


def _build_trace_excerpt(sample: EvalSample, max_chars: int = 500) -> str:
    """Build a short excerpt from the last few assistant messages."""
    parts: list[str] = []
    for msg in reversed(sample.messages):
        if (
            isinstance(msg, (ChatMessageAssistant, ChatMessageTool))
            and msg.text
            and msg.text.strip()
        ):
            parts.append(msg.text.strip())
        else:
            continue
        if sum(len(p) for p in parts) >= max_chars:
            break

    parts.reverse()
    excerpt = "\n---\n".join(parts)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "…"
    return excerpt


def _extract_failures_from_log(eval_log: EvalLog) -> list[EvalFailure]:
    """Extract failures from a parsed EvalLog object."""
    if not eval_log.samples:
        _log.debug("No samples in eval log %s", eval_log.location)
        return []

    server = _server_from_task_name(eval_log.eval.task)
    failures: list[EvalFailure] = []

    for sample in eval_log.samples:
        if not sample.scores:
            continue

        for _scorer_name, score_obj in sample.scores.items():
            value = score_obj.value
            value_str = str(value) if not isinstance(value, str) else value

            if value_str == CORRECT:
                continue

            failures.append(
                EvalFailure(
                    server=server,
                    scenario=_extract_input_text(sample),
                    tool_calls=_extract_tool_call_names(sample),
                    error=score_obj.explanation or "",
                    score=value_str,
                    trace_excerpt=_build_trace_excerpt(sample),
                )
            )

    return failures


def analyze_eval_log(log_path: str | Path) -> list[EvalFailure]:
    """Read an Inspect AI ``.eval`` log file and extract failures.

    A failure is any sample where the score value is not CORRECT (``"C"``).

    Args:
        log_path: Path to an ``.eval`` log file produced by ``inspect eval``.

    Returns:
        A list of :class:`EvalFailure` for every non-passing scenario.
    """
    path = Path(log_path)
    _log.info("Analyzing eval log: %s", path)
    eval_log = read_eval_log(path)
    return _extract_failures_from_log(eval_log)


def analyze_eval_dir(log_dir: str | Path) -> list[EvalFailure]:
    """Read all ``.eval`` files in a directory and extract failures.

    Args:
        log_dir: Directory containing ``.eval`` log files.

    Returns:
        Combined list of :class:`EvalFailure` from all logs in the directory.
    """
    directory = Path(log_dir)
    if not directory.is_dir():
        _log.warning("Not a directory: %s", directory)
        return []

    failures: list[EvalFailure] = []
    eval_files = sorted(directory.glob("*.eval"))
    for eval_file in eval_files:
        try:
            failures.extend(analyze_eval_log(eval_file))
        except Exception:
            _log.exception("Failed to read eval log: %s", eval_file)

    _log.info("Found %d failures across %d log files", len(failures), len(eval_files))
    return failures
