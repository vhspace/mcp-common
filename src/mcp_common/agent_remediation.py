"""Standard agent-facing text when MCP or CLI code hits exceptions.

Documents a consistent workflow: delegate to a subagent, search GitHub issues,
react or comment, open an issue if needed, then continue the primary task.
"""

from __future__ import annotations


def _github_issues_search_url(repo: str) -> str:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        return f"https://github.com/{repo}/issues"
    return f"https://github.com/{owner}/{name}/issues?q=is%3Aissue"


def _github_issues_new_url(repo: str) -> str:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        return f"https://github.com/{repo}/issues/new"
    return f"https://github.com/{owner}/{name}/issues/new"


def format_agent_exception_remediation(
    *,
    exception: BaseException,
    project_repo: str | None,
    issue_tracker_url: str | None,
    tool_or_command: str | None = None,
    version: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """Return stable markdown for agents when a tool or CLI command fails.

    Workflow (for the handling subagent or the same agent, as appropriate):

    1. Search existing issues on the project tracker.
    2. If a matching issue exists: add a thumbs-up if it is an exact duplicate of this
       failure; otherwise add a comment with new information (logs, repro, version).
    3. If no match: open a new issue, then return to and continue the primary task.

    Parameters
    ----------
    exception
        The exception that was raised.
    project_repo
        GitHub repository as ``owner/name``, used to build issue links.
    issue_tracker_url
        Optional non-GitHub tracker URL (used when ``project_repo`` is not set, or
        alongside it as an extra pointer).
    tool_or_command
        Optional MCP tool name or CLI subcommand for context.
    version
        Optional server or package version string.
    extra_lines
        Optional extra bullet lines to append under **Context**.
    """
    exc_type = type(exception).__name__
    exc_msg = str(exception).strip() or "(no message)"

    lines: list[str] = [
        "## Agent remediation (errors in MCP tools or CLI)",
        "",
        "When this fails, **use a subagent** (or a focused sub-task) to handle the "
        "incident so the **primary task can continue**.",
        "",
        "1. **Search GitHub issues** for this project (and this error).",
        "2. If you find a matching issue:",
        "   - **Exact duplicate** of this failure → add a **thumbs-up** (👍) on that issue.",
        "   - **New information** (logs, repro, version, environment) → **comment** on that issue.",
        "3. If **no** matching issue → **open a new issue**, then **continue the primary task**.",
        "",
        "---",
        "",
        "### This failure",
        "",
        f"- **Exception:** `{exc_type}` — {exc_msg}",
    ]

    if tool_or_command:
        lines.append(f"- **Tool / command:** `{tool_or_command}`")
    if version:
        lines.append(f"- **Version:** `{version}`")

    if project_repo:
        lines.extend(
            [
                f"- **GitHub repo:** `{project_repo}`",
                f"  - Search issues: {_github_issues_search_url(project_repo)}",
                f"  - New issue: {_github_issues_new_url(project_repo)}",
            ]
        )

    if issue_tracker_url:
        lines.append(f"- **Issue tracker:** {issue_tracker_url}")

    if extra_lines:
        lines.append("")
        lines.append("### Additional context")
        lines.append("")
        for row in extra_lines:
            lines.append(f"- {row}")

    return "\n".join(lines) + "\n"
