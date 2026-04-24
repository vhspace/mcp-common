"""Placeholder for tool-description quality checks.

Validates that MCP tool descriptions are clear, unambiguous, and don't
conflict with tools exposed by other servers that an agent might see
simultaneously.
"""

from __future__ import annotations

from pydantic import BaseModel


class DescriptionIssue(BaseModel):
    """A quality problem found in a single tool description."""

    tool_name: str
    """Fully-qualified tool name (``server.tool``)."""

    issue_type: str
    """Category of the issue (e.g. ``"too_vague"``, ``"missing_example"``)."""

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
    """Cosine similarity score between the two descriptions."""

    explanation: str
    """Why these descriptions may confuse an agent."""


def check_description_quality(server_module: str) -> list[DescriptionIssue]:
    """Score every tool description exposed by *server_module*.

    Checks include:
    - Minimum description length and specificity.
    - Presence of at least one usage example.
    - No ambiguous phrases ("this tool does stuff").
    - Correct grammar and formatting.

    Args:
        server_module: Dotted Python import path to the MCP server module
            (e.g. ``"netbox_mcp.server"``).

    Returns:
        A list of issues found, empty if all descriptions pass.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #44.
    """
    raise NotImplementedError("See issue #44")


def check_similarity_conflicts(
    server_modules: list[str],
) -> list[SimilarityConflict]:
    """Detect inter-server description conflicts.

    Compares tool descriptions across all *server_modules* to find pairs
    whose descriptions are similar enough that an agent might confuse them.

    Args:
        server_modules: List of dotted Python import paths to MCP server
            modules to compare.

    Returns:
        A list of detected conflicts, empty if no problematic overlaps.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #44.
    """
    raise NotImplementedError("See issue #44")
