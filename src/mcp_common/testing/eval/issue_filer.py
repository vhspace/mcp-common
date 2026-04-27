"""Automatic GitHub issue creation from eval failures.

Converts :class:`~mcp_common.testing.eval.analyzer.EvalFailure` records into
GitHub issues, deduplicating against existing open issues to avoid noise.
Uses the ``gh`` CLI (assumed to be pre-installed and authenticated).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path

from mcp_common.testing.eval.analyzer import EvalFailure
from mcp_common.testing.eval.repo_discovery import resolve_server_to_repo

_log = logging.getLogger(__name__)


def _fingerprint(failure: EvalFailure) -> str:
    """Compute a stable short fingerprint for deduplication.

    Hashes (server, scenario input, score, first tool call) to produce a
    16-character hex digest.
    """
    first_tool = failure.tool_calls[0] if failure.tool_calls else ""
    error_prefix = (failure.error or "")[:100]
    raw = "|".join([failure.server, failure.scenario, failure.score, first_tool, error_prefix])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_existing_issue_titles(repo: str) -> list[str]:
    """Fetch titles of open eval-related issues from a GitHub repo.

    Uses ``gh issue list --search "eval:" --json title``.
    Returns an empty list on any failure (missing repo, no auth, etc.).
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--search",
                "eval: in:title",
                "--json",
                "title",
                "--limit",
                "200",  # practical cap; increase if repos accumulate more eval issues
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _log.debug("gh issue list failed for %s: %s", repo, result.stderr.strip())
            return []
        data = json.loads(result.stdout)
        return [item["title"] for item in data if "title" in item]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        _log.debug("Could not fetch existing issues for %s", repo, exc_info=True)
        return []


def _resolve_github_repo(
    server: str,
    *,
    workspace: Path | None,
    repo_prefix: str,
) -> str:
    """Map a server name to its ``owner/repo`` GitHub identifier."""
    if workspace is not None:
        info = resolve_server_to_repo(server, workspace)
        if info is not None:
            return info.github_repo
    return f"{repo_prefix}/{server}"


def deduplicate(
    failures: list[EvalFailure],
    *,
    repo: str | None = None,
    repo_prefix: str = "vhspace",
    workspace: Path | None = None,
) -> list[EvalFailure]:
    """Remove duplicate failures.

    Deduplication is two-phase:
    1. Within the batch — identical fingerprints are collapsed.
    2. Against existing GitHub issues — failures whose fingerprint
       appears in an open issue title are skipped.

    Args:
        failures: Failures to deduplicate.
        repo: If provided, check only this repo for existing issues.
            If ``None``, check each failure's server repo individually.
        repo_prefix: GitHub org prefix (default ``"vhspace"``).
        workspace: Workspace root for dynamic repo discovery.
            Falls back to ``repo_prefix/server`` when ``None``.
    """
    seen: set[str] = set()
    unique: list[EvalFailure] = []

    for f in failures:
        fp = _fingerprint(f)
        if fp in seen:
            continue
        seen.add(fp)
        unique.append(f)

    if not repo:
        by_server: dict[str, list[EvalFailure]] = {}
        for f in unique:
            by_server.setdefault(f.server, []).append(f)
        filtered: list[EvalFailure] = []
        for server, server_failures in by_server.items():
            gh_repo = _resolve_github_repo(server, workspace=workspace, repo_prefix=repo_prefix)
            existing_titles = _get_existing_issue_titles(gh_repo)
            for f in server_failures:
                fp = _fingerprint(f)
                if any(fp in title for title in existing_titles):
                    _log.debug("Skipping already-filed failure: %s", fp)
                    continue
                filtered.append(f)
        return filtered

    existing_titles = _get_existing_issue_titles(repo)
    return [f for f in unique if not any(_fingerprint(f) in title for title in existing_titles)]


def _format_issue_title(failure: EvalFailure) -> str:
    """Format a GitHub issue title for an eval failure."""
    scenario_truncated = failure.scenario[:60]
    if len(failure.scenario) > 60:
        scenario_truncated += "…"
    fp = _fingerprint(failure)
    return f"eval: {scenario_truncated} [{failure.score}] ({fp})"


def _format_issue_body(failure: EvalFailure) -> str:
    """Format a GitHub issue body with structured sections."""
    tools_called = ", ".join(failure.tool_calls) if failure.tool_calls else "(none)"

    expected_tools = ""
    if failure.error:
        match = re.search(r"expected \[([^\]]+)\]", failure.error)
        if match:
            expected_tools = match.group(1)
    if not expected_tools:
        expected_tools = "(see scorer explanation)"

    # Heuristic: infer fix category from scorer explanation keywords.
    # This is intentionally simple; structured categorization can be added later.
    fix_category = "tool-selection"
    if "completion" in failure.error.lower():
        fix_category = "task-completion"
    elif "interface" in failure.error.lower():
        fix_category = "interface-choice"
    elif not failure.tool_calls:
        fix_category = "no-tools-called"

    return f"""## Summary

**Server:** `{failure.server}`
**Score:** `{failure.score}`
**Fingerprint:** `{_fingerprint(failure)}`

## Scorer Explanation

{failure.error or "(no explanation)"}

## Eval Trace

```
{failure.trace_excerpt or "(no trace available)"}
```

## Tools Called

{tools_called}

## Expected Tools

{expected_tools}

## Suggested Fix Category

`{fix_category}`
"""


def file_issues(
    failures: list[EvalFailure],
    *,
    dry_run: bool = True,
    repo_prefix: str = "vhspace",
    workspace: Path | None = None,
) -> list[str]:
    """File GitHub issues for eval failures.

    Creates one issue per failure using the ``gh`` CLI.

    Args:
        failures: List of failures to file.
        dry_run: If ``True`` (default), print what would be filed without
            creating issues.
        repo_prefix: GitHub org prefix (default ``"vhspace"``).
        workspace: Workspace root for dynamic repo discovery.
            Falls back to ``repo_prefix/server`` when ``None``.

    Returns:
        List of created issue URLs (empty in dry-run mode).
    """
    urls: list[str] = []

    for failure in failures:
        repo = _resolve_github_repo(failure.server, workspace=workspace, repo_prefix=repo_prefix)
        title = _format_issue_title(failure)
        body = _format_issue_body(failure)

        if dry_run:
            _log.info("[DRY RUN] Would file issue in %s: %s", repo, title)
            print(f"[DRY RUN] {repo}: {title}")
            continue

        try:
            result = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    repo,
                    "--title",
                    title,
                    "--body",
                    body,
                    "--label",
                    "eval-failure",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                urls.append(url)
                _log.info("Filed issue: %s", url)
            else:
                _log.error("Failed to file issue in %s: %s", repo, result.stderr.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            _log.exception("Failed to run gh CLI for %s", repo)

    return urls
