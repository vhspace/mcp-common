"""Dispatch a coding agent to fix eval failures.

Supports multiple agent backends:
- Claude Code (``claude``)
- Cursor CLI agent (``cursor-agent``) — placeholder until the Cursor agent
  CLI stabilises

The agent receives the eval failure context (issue URL, trace excerpt,
suggested fix) and attempts to create a fix branch + PR.

Because the infrastructure is behind VPN, this runs locally — not from
GitHub Actions.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import typer

from mcp_common.testing.eval.analyzer import EvalFailure

_log = logging.getLogger(__name__)

_SUPPORTED_BACKENDS = ("claude", "cursor")


def _extract_issue_number(issue_url: str) -> str:
    """Pull the trailing issue number from a GitHub issue URL."""
    match = re.search(r"/issues/(\d+)", issue_url)
    return match.group(1) if match else "unknown"


def _build_remediation_prompt(failure: EvalFailure, issue_url: str) -> str:
    """Build a prompt for a coding agent to fix an eval failure."""
    issue_number = _extract_issue_number(issue_url)
    tools_called = ", ".join(failure.tool_calls) if failure.tool_calls else "None"

    expected_tools = "See issue for details"
    if failure.error:
        match = re.search(r"expected \[([^\]]+)\]", failure.error)
        if match:
            expected_tools = match.group(1)

    return f"""Fix the eval failure described in {issue_url}

## What happened
An agent was asked: "{failure.scenario}"

## What went wrong
{failure.error}

## Eval trace
{failure.trace_excerpt}

## Tools the agent called
{tools_called}

## Expected tools
{expected_tools}

## What to fix
Look at the MCP tool implementation and/or CLI command that the agent tried to use.
Common fixes include:
- Improving tool descriptions so the agent picks the right tool
- Adding error handling so failures are reported clearly
- Fixing argument validation so bad inputs get helpful errors
- Adding missing return information to tool descriptions

## Instructions
1. Create a branch named fix/eval-{issue_number}
2. Make the minimal fix needed
3. Run existing tests to verify nothing breaks
4. Open a PR referencing {issue_url}
"""


def remediate_failure(
    failure: EvalFailure,
    issue_url: str,
    workspace_root: str | Path = "/workspaces/together",
    agent_backend: str = "claude",
    dry_run: bool = True,
) -> str | None:
    """Dispatch a coding agent to fix a single eval failure.

    Args:
        failure: The eval failure to fix.
        issue_url: URL of the filed GitHub issue.
        workspace_root: Root of the workspace (default ``/workspaces/together``).
        agent_backend: Which agent to use (``"claude"`` or ``"cursor"``).
        dry_run: If ``True``, print the command that would be run without executing.

    Returns:
        The PR URL if created, ``None`` otherwise.
    """
    if agent_backend not in _SUPPORTED_BACKENDS:
        _log.error(
            "Unsupported agent backend: %s (choose from %s)", agent_backend, _SUPPORTED_BACKENDS
        )
        return None

    repo_dir = Path(workspace_root) / failure.server
    prompt = _build_remediation_prompt(failure, issue_url)

    if agent_backend == "claude":
        return _run_claude(prompt, repo_dir, issue_url, dry_run=dry_run)

    # cursor backend
    return _run_cursor(prompt, repo_dir, issue_url, dry_run=dry_run)


def _run_claude(
    prompt: str,
    repo_dir: Path,
    issue_url: str,
    *,
    dry_run: bool,
) -> str | None:
    """Dispatch fix via Claude Code CLI."""
    allowed_tools = "Edit,Write,Bash(git*),Bash(gh*),Read,Glob,Grep"
    cmd = [
        "claude",
        "-p",
        f"Fix {issue_url}: {prompt}",
        "--allowedTools",
        allowed_tools,
    ]

    if dry_run:
        typer.echo(f"[DRY RUN] would run in {repo_dir}:")
        typer.echo(
            f"  cd {repo_dir} && claude -p 'Fix {issue_url}: ...' --allowedTools \"{allowed_tools}\""
        )
        return None

    if not shutil.which("claude"):
        _log.warning("claude CLI not found on PATH — install Claude Code first")
        typer.echo("Warning: claude CLI not found on PATH. Skipping remediation.")
        return None

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            pr_url = _extract_pr_url(result.stdout)
            if pr_url:
                _log.info("Agent opened PR: %s", pr_url)
                return pr_url
            _log.info("Agent completed but no PR URL detected in output")
            return None
        _log.error("Agent exited with code %d: %s", result.returncode, result.stderr[:500])
        return None
    except subprocess.TimeoutExpired:
        _log.error("Agent timed out after 300s for %s", issue_url)
        return None
    except FileNotFoundError:
        _log.error("claude CLI not found")
        return None


def _run_cursor(
    prompt: str,
    repo_dir: Path,
    issue_url: str,
    *,
    dry_run: bool,
) -> str | None:
    """Placeholder for Cursor agent CLI dispatch.

    The Cursor agent API is not yet stable for headless CLI invocation.
    For now this backend only prints the prompt for manual paste.
    """
    if dry_run:
        typer.echo("[DRY RUN] cursor agent not yet supported for headless dispatch")
        typer.echo(f"  Target repo: {repo_dir}")
        typer.echo(f"  Issue: {issue_url}")
        return None

    typer.echo("Cursor agent headless dispatch is not yet supported.")
    typer.echo("Copy the prompt below and paste it into a Cursor agent chat:\n")
    typer.echo(prompt)
    return None


def _extract_pr_url(output: str) -> str | None:
    """Scan agent stdout for a GitHub PR URL."""
    match = re.search(r"https://github\.com/[^\s]+/pull/\d+", output)
    return match.group(0) if match else None


def remediate_batch(
    failures: list[EvalFailure],
    issue_urls: dict[str, str],
    workspace_root: str | Path = "/workspaces/together",
    agent_backend: str = "claude",
    dry_run: bool = True,
) -> list[str]:
    """Dispatch agents for multiple failures.

    Args:
        failures: Eval failures to remediate.
        issue_urls: Mapping of ``failure.server + "|" + failure.scenario``
            keys to GitHub issue URLs.
        workspace_root: Root of the workspace.
        agent_backend: Agent backend name.
        dry_run: If ``True``, only preview commands.

    Returns:
        List of PR URLs that were successfully opened.
    """
    pr_urls: list[str] = []
    for failure in failures:
        key = f"{failure.server}|{failure.scenario}"
        url = issue_urls.get(key)
        if not url:
            _log.warning("No issue URL for failure key %s — skipping", key)
            continue

        pr_url = remediate_failure(
            failure,
            url,
            workspace_root=workspace_root,
            agent_backend=agent_backend,
            dry_run=dry_run,
        )
        if pr_url:
            pr_urls.append(pr_url)

    return pr_urls
