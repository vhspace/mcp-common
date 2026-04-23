"""Placeholder for automatic GitHub issue creation from eval failures.

Converts :class:`~mcp_common.testing.eval.analyzer.EvalFailure` records into
GitHub issues, deduplicating against existing open issues to avoid noise.
"""

from __future__ import annotations

from mcp_common.testing.eval.analyzer import EvalFailure


def file_issues(
    failures: list[EvalFailure],
    *,
    dry_run: bool = True,
) -> list[str]:
    """File GitHub issues for eval failures.

    Creates one issue per unique failure, including the scenario prompt,
    expected vs. actual tool calls, score, and a trace excerpt.

    Args:
        failures: Eval failures to file as issues.
        dry_run: When ``True`` (default), only print what *would* be filed
            without creating real issues.

    Returns:
        A list of issue URLs (or dry-run descriptions).

    Raises:
        NotImplementedError: Always; implementation tracked in issue #47.
    """
    raise NotImplementedError("See issue #47")


def deduplicate(failures: list[EvalFailure]) -> list[EvalFailure]:
    """Remove duplicate failures before filing.

    Groups failures by ``(server, scenario)`` and keeps only the
    representative with the lowest score from each group.

    Args:
        failures: Raw list of failures, possibly with duplicates.

    Returns:
        De-duplicated list of failures.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #47.
    """
    raise NotImplementedError("See issue #47")
