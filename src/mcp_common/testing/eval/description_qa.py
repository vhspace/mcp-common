"""Tool-description quality checks for CI.

Validates that MCP tool descriptions are clear, unambiguous, and don't
conflict with tools exposed by other servers that an agent might see
simultaneously.

Uses fast heuristic rules (no LLM) so it can run as a cheap CI gate.
"""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Callable, Coroutine
from difflib import SequenceMatcher
from typing import Any, Literal, TypeVar

import anyio.from_thread
from fastmcp import Client, FastMCP
from pydantic import BaseModel

_T = TypeVar("_T")

IssueType = Literal[
    "too_vague",
    "missing_parameters",
    "missing_error_info",
    "too_long",
    "missing_return_info",
]

_MIN_DESC_LENGTH = 20
_MAX_DESC_LENGTH = 500
_SIMILARITY_THRESHOLD = 0.6


def _run_async(coro_fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Call an async zero-arg function from a synchronous context.

    Uses ``asyncio.run`` when no event loop is active, or
    ``anyio.from_thread.run`` when called from within an existing
    async context (e.g. a sync helper invoked from a pytest-anyio test).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    return anyio.from_thread.run(coro_fn)  # type: ignore[return-value]


_RETURN_PATTERNS = re.compile(
    r"(?i)\b(returns?|result|output|response|yields?|produces?|gives?\s+back)\b"
)
_ERROR_PATTERNS = re.compile(
    r"(?i)\b(error|fail|raises?|exception|invalid|not\s+found|missing|unable|cannot)\b"
)


class DescriptionIssue(BaseModel):
    """A quality problem found in a single tool description."""

    tool_name: str
    """Fully-qualified tool name (``server.tool``)."""

    issue_type: IssueType
    """Category of the issue."""

    message: str
    """Human-readable explanation of what's wrong."""

    score: float = 0.0
    """Quality score between 0 (worst) and 1 (best)."""


class SimilarityConflict(BaseModel):
    """A detected conflict between tool descriptions across servers."""

    tool_a: str
    """First tool's fully-qualified name."""

    tool_b: str
    """Second tool's fully-qualified name."""

    similarity: float
    """Sequence-matcher similarity ratio between the two descriptions."""

    score: float
    """Alias kept for downstream consumers (same value as ``similarity``)."""

    explanation: str
    """Why these descriptions may confuse an agent."""


def _get_fastmcp_instance(server_module: str) -> FastMCP:
    """Import *server_module* and return its ``FastMCP`` instance.

    Looks for a module-level attribute named ``mcp``, ``app``, or ``server``.
    """
    mod = importlib.import_module(server_module)
    for attr in ("mcp", "app", "server"):
        obj = getattr(mod, attr, None)
        if isinstance(obj, FastMCP):
            return obj
    raise ValueError(
        f"Module {server_module!r} has no FastMCP instance named 'mcp', 'app', or 'server'"
    )


ToolInfo = tuple[str, str, list[str]]  # (name, description, param_names)


async def _collect_tools(server: FastMCP) -> list[ToolInfo]:
    """List tools from a FastMCP server via the MCP client protocol."""
    async with Client(server) as client:
        tools = await client.list_tools()
    return [
        (
            t.name,
            t.description or "",
            [p_name for p_name in (t.inputSchema or {}).get("properties", {})],
        )
        for t in tools
    ]


def _check_tool(
    server_name: str, name: str, description: str, param_names: list[str]
) -> list[DescriptionIssue]:
    """Run heuristic checks against a single tool's description."""
    fq = f"{server_name}.{name}"
    issues: list[DescriptionIssue] = []

    if len(description) < _MIN_DESC_LENGTH:
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="too_vague",
                message=(
                    f"Description is only {len(description)} chars "
                    f"(minimum {_MIN_DESC_LENGTH}). "
                    "Add a sentence explaining what the tool does."
                ),
                score=max(0.0, len(description) / _MIN_DESC_LENGTH),
            )
        )

    if len(description) > _MAX_DESC_LENGTH:
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="too_long",
                message=(
                    f"Description is {len(description)} chars "
                    f"(maximum {_MAX_DESC_LENGTH}). "
                    "Trim to avoid wasting tokens in the system prompt."
                ),
                score=max(0.0, 1 - (len(description) - _MAX_DESC_LENGTH) / _MAX_DESC_LENGTH),
            )
        )

    if param_names and not any(p.lower() in description.lower() for p in param_names):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_parameters",
                message=(
                    f"Tool has parameters {param_names!r} but the description "
                    "doesn't mention any of them."
                ),
                score=0.0,
            )
        )

    if not _ERROR_PATTERNS.search(description):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_error_info",
                message="Description doesn't mention error cases or failure modes.",
                score=0.0,
            )
        )

    if not _RETURN_PATTERNS.search(description):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_return_info",
                message="Description doesn't describe what the tool returns.",
                score=0.0,
            )
        )

    return issues


def check_description_quality(server_module: str) -> list[DescriptionIssue]:
    """Score every tool description exposed by *server_module*.

    Checks include:

    - **too_vague** — description under 20 chars or non-specific.
    - **missing_parameters** — tool has params but description ignores them.
    - **missing_error_info** — no mention of error / failure behaviour.
    - **too_long** — description exceeds 500 chars (wastes tokens).
    - **missing_return_info** — no mention of what the tool returns.

    The function is synchronous; async internals are handled transparently.

    Args:
        server_module: Dotted Python import path to the MCP server module
            (e.g. ``"netbox_mcp.server"``).

    Returns:
        A list of issues found, empty if all descriptions pass.
    """
    server = _get_fastmcp_instance(server_module)
    tools = _run_async(lambda: _collect_tools(server))
    server_name = server.name

    issues: list[DescriptionIssue] = []
    for name, description, param_names in tools:
        issues.extend(_check_tool(server_name, name, description, param_names))
    return issues


def _collect_all_tools(
    server_modules: list[str],
) -> list[tuple[str, str, str]]:
    """Return ``(server_name, tool_name, description)`` across all servers."""

    async def _gather() -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        for module in server_modules:
            server = _get_fastmcp_instance(module)
            tools = await _collect_tools(server)
            for name, desc, _ in tools:
                results.append((server.name, name, desc))
        return results

    return _run_async(_gather)


def check_similarity_conflicts(
    server_modules: list[str],
) -> list[SimilarityConflict]:
    """Detect inter-server description conflicts.

    Compares tool descriptions across all *server_modules* to find pairs
    whose descriptions are similar enough that an agent might confuse them.

    Uses ``difflib.SequenceMatcher`` for fast, deterministic comparison
    (no LLM required).

    The function is synchronous; async internals are handled transparently.

    Args:
        server_modules: List of dotted Python import paths to MCP server
            modules to compare.

    Returns:
        A list of detected conflicts, empty if no problematic overlaps.
    """
    all_tools = _collect_all_tools(server_modules)
    conflicts: list[SimilarityConflict] = []

    for i, (srv_a, name_a, desc_a) in enumerate(all_tools):
        for srv_b, name_b, desc_b in all_tools[i + 1 :]:
            if srv_a == srv_b:
                continue
            if not desc_a or not desc_b:
                continue
            ratio = SequenceMatcher(None, desc_a.lower(), desc_b.lower()).ratio()
            if ratio > _SIMILARITY_THRESHOLD:
                conflicts.append(
                    SimilarityConflict(
                        tool_a=f"{srv_a}.{name_a}",
                        tool_b=f"{srv_b}.{name_b}",
                        similarity=round(ratio, 3),
                        score=round(ratio, 3),
                        explanation=(
                            f"Descriptions are {ratio:.0%} similar. "
                            "An agent may confuse these tools when both servers are loaded."
                        ),
                    )
                )

    return conflicts
