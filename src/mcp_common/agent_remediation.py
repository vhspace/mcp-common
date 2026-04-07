"""Standard agent-facing text when MCP or CLI code hits exceptions.

Documents a consistent workflow: delegate to a subagent, search GitHub issues,
react or comment, open an issue if needed, then continue the primary task.

Also provides integration helpers for Typer CLI apps and FastMCP tool handlers.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import typer

F = TypeVar("F", bound=Callable[..., Any])


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
    try:
        return _build_remediation_text(
            exception=exception,
            project_repo=project_repo,
            issue_tracker_url=issue_tracker_url,
            tool_or_command=tool_or_command,
            version=version,
            extra_lines=extra_lines,
        )
    except Exception:
        return (
            "## Agent remediation (errors in MCP tools or CLI)\n\n"
            "An error occurred and the remediation formatter also failed.\n"
            "Search the project's issue tracker for this error and file an issue if needed.\n"
        )


def _build_remediation_text(
    *,
    exception: BaseException,
    project_repo: str | None,
    issue_tracker_url: str | None,
    tool_or_command: str | None,
    version: str | None,
    extra_lines: list[str] | None,
) -> str:
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


# ---------------------------------------------------------------------------
# CLI integration (Typer)
# ---------------------------------------------------------------------------


def install_cli_exception_handler(
    app: typer.Typer,
    *,
    project_repo: str | None = None,
    issue_tracker_url: str | None = None,
    version: str | None = None,
) -> None:
    """Register a global Typer callback that catches unhandled exceptions.

    On failure the handler prints a user-safe message plus the standard
    remediation block to stderr and exits with code 1.

    Usage::

        app = typer.Typer()
        install_cli_exception_handler(app, project_repo="myorg/my-cli")
    """
    original_callback = app.registered_callback

    def _wrapper_callback() -> None:  # pragma: no cover - thin shim
        pass

    if original_callback is None or original_callback.callback is None:
        app.callback(invoke_without_command=True)(_wrapper_callback)

    _orig_invoke = app.__class__.__call__

    def _patched_call(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return _orig_invoke(self, *args, **kwargs)
        except SystemExit:
            raise
        except Exception as exc:
            tb = traceback.format_exc()
            remediation = format_agent_exception_remediation(
                exception=exc,
                project_repo=project_repo,
                issue_tracker_url=issue_tracker_url,
                version=version,
                extra_lines=[f"Traceback (last 5 lines):\n```\n{_last_n_lines(tb, 5)}\n```"],
            )
            print(f"Error: {exc}", file=sys.stderr)
            print(remediation, file=sys.stderr)
            raise SystemExit(1) from exc

    app.__class__.__call__ = _patched_call  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# MCP integration (FastMCP)
# ---------------------------------------------------------------------------


def mcp_tool_error_with_remediation(
    exception: BaseException,
    *,
    project_repo: str | None = None,
    issue_tracker_url: str | None = None,
    tool_name: str | None = None,
    version: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """Format an MCP tool error response that includes the remediation block.

    Returns a string suitable for raising as ``ToolError(text)`` or returning
    as structured error text from a FastMCP tool handler::

        from fastmcp.exceptions import ToolError
        from mcp_common.agent_remediation import mcp_tool_error_with_remediation

        try:
            result = do_work()
        except Exception as exc:
            raise ToolError(
                mcp_tool_error_with_remediation(exc, project_repo="myorg/my-mcp", tool_name="my_tool")
            ) from exc
    """
    return format_agent_exception_remediation(
        exception=exception,
        project_repo=project_repo,
        issue_tracker_url=issue_tracker_url,
        tool_or_command=tool_name,
        version=version,
        extra_lines=extra_lines,
    )


def mcp_remediation_wrapper(
    *,
    project_repo: str | None = None,
    issue_tracker_url: str | None = None,
    version: str | None = None,
) -> Callable[[F], F]:
    """Decorator for async FastMCP tool functions that catches exceptions.

    On failure the original exception is re-raised as a ``ToolError`` with
    the remediation block appended, so the calling agent sees both the
    error and the issue-filing guidance::

        @mcp.tool()
        @mcp_remediation_wrapper(project_repo="myorg/my-mcp")
        async def my_tool(arg: str) -> str:
            ...
    """
    import asyncio
    import functools

    def _handle_exc(exc: Exception, fn_name: str) -> None:
        from fastmcp.exceptions import ToolError

        if isinstance(exc, ToolError):
            raise
        try:
            msg = mcp_tool_error_with_remediation(
                exc,
                project_repo=project_repo,
                issue_tracker_url=issue_tracker_url,
                tool_name=fn_name,
                version=version,
            )
        except Exception:
            msg = f"{type(exc).__name__}: {exc}"
        raise ToolError(msg) from exc

    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    _handle_exc(exc, fn.__name__)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                _handle_exc(exc, fn.__name__)

        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _last_n_lines(text: str, n: int) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def _is_awaitable(obj: object) -> bool:
    """Check if an object is awaitable (coroutine, Future, etc.)."""
    from collections.abc import Awaitable as AwaitableABC

    return isinstance(obj, AwaitableABC)
