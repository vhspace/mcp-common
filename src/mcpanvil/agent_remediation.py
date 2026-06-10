"""Standard agent-facing text when MCP or CLI code hits exceptions.

Documents a consistent workflow: delegate to a subagent, search GitHub issues,
react or comment, open an issue if needed, then continue the primary task.

Also provides integration helpers for Typer CLI apps and FastMCP tool handlers.
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import typer

F = TypeVar("F", bound=Callable[..., Any])

#: Attribute set on the slim ``ToolError`` raised by :func:`mcp_remediation_wrapper`
#: so :func:`install_cli_exception_handler` recognizes an already-formatted slim
#: remediation error and passes its message through verbatim — instead of
#: re-stamping a second ``(ref: …)`` / "This failure has been logged." line.
_SLIM_TOOL_ERROR_MARKER = "__mcp_slim__"

#: Companion marker recording that the wrapper already emitted the failure on the
#: trace channel, so the CLI handler can skip a duplicate trace record for the
#: same failure while still passing the slim message through.
_SLIM_TRACE_LOGGED_MARKER = "__mcp_trace_logged__"


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
    logger: logging.Logger | None = None,
) -> None:
    """Register a global Typer callback that catches unhandled exceptions.

    On failure the **caller** (stderr) receives only a clean, terse error —
    the exception type, message, and a fingerprint reference — followed by
    ``"This failure has been logged."`` and exit code 1::

        Error: <ExcType>: <msg> (ref: <16-hex-fingerprint>)
        This failure has been logged.

    The agent remediation block (issue-filing guidance + traceback) is a
    diagnostic artifact **for the trace/diagnostic log only**: it is routed to
    :func:`mcpanvil.logging.log_trace_event` (along with the fingerprint,
    ``project_repo`` and ``version``) where a separate triage agent consumes
    it. It is **never** shown to the calling agent. This mirrors
    :func:`mcp_remediation_wrapper`'s MCP ``ToolError`` behavior, so CLI and
    MCP failures correlate by fingerprint in the trace log.

    Args:
        logger: Trace logger for the diagnostic record. When ``None`` a module
            default logger (``mcpanvil.agent_remediation``) is used so the
            trace event is **always** emitted; sink/routing remains the
            application's responsibility (configure via
            :func:`mcpanvil.logging.setup_logging`).

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
            _route_cli_failure(
                exc,
                project_repo=project_repo,
                issue_tracker_url=issue_tracker_url,
                version=version,
                logger=logger,
            )
            raise SystemExit(1) from exc

    app.__class__.__call__ = _patched_call  # type: ignore[method-assign]


def _route_cli_failure(
    exc: BaseException,
    *,
    project_repo: str | None,
    issue_tracker_url: str | None,
    version: str | None,
    logger: logging.Logger | None,
) -> None:
    """Send the full remediation to the trace log; print a terse error to stderr.

    The remediation block (issue-filing guidance + traceback) is a diagnostic
    artifact for the trace log only, consumed by a separate triage agent. The
    caller (stderr) only ever sees a slim two-line error mirroring
    :func:`mcp_remediation_wrapper`'s ``ToolError`` shape.

    When the propagating exception is *already* a slim remediation ``ToolError``
    raised by :func:`mcp_remediation_wrapper` (tagged with
    :data:`_SLIM_TOOL_ERROR_MARKER`), its message is passed through **verbatim**
    so the caller sees exactly one terse line — one ``(ref: …)`` and one
    "This failure has been logged." sentinel — instead of a redundant second
    stamp. The failure is recorded
    on the trace channel exactly once: the wrapper already logged it (so the
    handler skips a duplicate), or — if the wrapper had no logger — the handler
    records it here.
    """
    from fastmcp.exceptions import ToolError

    if isinstance(exc, ToolError) and getattr(exc, _SLIM_TOOL_ERROR_MARKER, False):
        # Already-slim remediation ToolError: skip a duplicate trace record when
        # the wrapper already logged the failure; otherwise record it once here.
        if not getattr(exc, _SLIM_TRACE_LOGGED_MARKER, False):
            _log_cli_trace_event(
                exc,
                project_repo=project_repo,
                issue_tracker_url=issue_tracker_url,
                version=version,
                logger=logger,
            )
        # Pass the slim message through verbatim — no second ref / "logged" line.
        print(f"Error: {exc}", file=sys.stderr)
        return

    # Raw exception path: a CLI-level error NOT produced by the wrapper. Emit the
    # terse single-ref line + one "logged" sentinel, exactly as before.
    fingerprint = _log_cli_trace_event(
        exc,
        project_repo=project_repo,
        issue_tracker_url=issue_tracker_url,
        version=version,
        logger=logger,
    )
    exc_msg = _flatten_exception_message(exc)
    # Caller-facing stderr: terse error only. No remediation block, no traceback.
    print(f"Error: {type(exc).__name__}: {exc_msg} (ref: {fingerprint})", file=sys.stderr)
    print("This failure has been logged.", file=sys.stderr)


def _log_cli_trace_event(
    exc: BaseException,
    *,
    project_repo: str | None,
    issue_tracker_url: str | None,
    version: str | None,
    logger: logging.Logger | None,
) -> str:
    """Record the full failure diagnostic on the dedicated trace channel.

    Returns the error fingerprint (reused in the caller-facing terse line on the
    raw-exception path). The diagnostic — agent-remediation guidance plus a
    truncated traceback — is routed to :func:`mcpanvil.logging.log_trace_event`
    (trace channel only) and is **never** shown to the caller. Always emits (falls
    back to a module default logger when ``logger`` is ``None``) so the record is
    never dropped. ``exc_info`` is intentionally omitted: the traceback rides in
    structured fields (``remediation`` / ``traceback``) so that even if logging is
    routed to stderr (e.g. ``logging.basicConfig``) the caller never sees a
    traceback.
    """
    from mcpanvil.logging import compute_error_fingerprint, log_trace_event

    try:
        fingerprint = compute_error_fingerprint(exc)
    except Exception:
        fingerprint = "unknown"

    exc_msg = _flatten_exception_message(exc)
    tb = traceback.format_exc()
    remediation = format_agent_exception_remediation(
        exception=exc,
        project_repo=project_repo,
        issue_tracker_url=issue_tracker_url,
        version=version,
        extra_lines=[f"Traceback (last 5 lines):\n```\n{_last_n_lines(tb, 5)}\n```"],
    )

    active_logger = logger if logger is not None else logging.getLogger(__name__)
    try:
        log_trace_event(
            active_logger,
            f"CLI failed: {exc_msg}",
            exc_info=False,
            error_fingerprint=fingerprint,
            project_repo=project_repo,
            version=version,
            remediation=remediation,
            traceback=tb,
        )
    except Exception:
        pass

    return fingerprint


def _flatten_exception_message(exc: BaseException) -> str:
    """Return ``str(exc)`` collapsed to a single line (safe on broken ``__str__``)."""
    try:
        text = str(exc)
    except Exception:
        text = "(unprintable exception)"
    return text.replace("\r", "").replace("\n", " ")


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

    .. deprecated::
        **Do not use for caller-facing tool error paths.** This embeds the full
        remediation block (issue-filing guidance + traceback) in the string the
        calling agent receives, which violates the trace-log-only design for
        remediation (the block is a diagnostic artifact for a separate triage
        agent, not for the caller).
        Instead, decorate tools with :func:`mcp_remediation_wrapper` (or raise a
        slim ``ToolError`` yourself) and rely on the trace log via
        :func:`mcpanvil.logging.log_trace_event` for the remediation/triage
        guidance. This helper remains only for non-caller-facing composition
        (e.g. building a remediation string to write to a diagnostic sink).

    Returns a string containing the remediation block::

        from mcpanvil.agent_remediation import mcp_tool_error_with_remediation

        # For a DIAGNOSTIC sink only — never as the ToolError shown to the caller.
        diagnostic_text = mcp_tool_error_with_remediation(
            exc, project_repo="myorg/my-mcp", tool_name="my_tool"
        )
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
    logger: logging.Logger | None = None,
) -> Callable[[F], F]:
    """Decorator for async (or sync) FastMCP tool functions that catches exceptions.

    On failure the caller receives a **slim** ``ToolError`` — the exception
    type, message, and a fingerprint reference, plus a one-line instruction to
    continue::

        <ExcType>: <msg> (ref: <16-hex-fingerprint>)
        This failure has been logged. Continue with the primary task.

    The remediation guidance (issue-filing workflow, traceback) is **not**
    included in the ``ToolError`` — it is a diagnostic artifact routed to the
    **trace/diagnostic log** via :func:`mcpanvil.logging.log_trace_event`
    (with the fingerprint, ``tool_name``, ``project_repo`` and ``version``),
    where a separate triage agent consumes it. The caller never sees the
    remediation block::

        @mcp.tool()
        @mcp_remediation_wrapper(project_repo="myorg/my-mcp", logger=logger)
        async def my_tool(arg: str) -> str:
            ...

    Args:
        logger: Optional trace logger; when provided a trace event carrying the
            full failure context is emitted before re-raising the slim
            ``ToolError``. When ``None`` no trace event is emitted (the caller
            still gets the slim error).
    """
    import asyncio
    import functools

    def _handle_exc(exc: Exception, fn_name: str) -> None:
        from fastmcp.exceptions import ToolError

        from mcpanvil.logging import compute_error_fingerprint, log_trace_event

        if isinstance(exc, ToolError):
            raise

        try:
            fingerprint = compute_error_fingerprint(exc)
        except Exception:
            fingerprint = "unknown"

        trace_logged = False
        if logger is not None:
            try:
                log_trace_event(
                    logger,
                    f"{fn_name} failed",
                    exc_info=exc,
                    error_fingerprint=fingerprint,
                    tool_name=fn_name,
                    project_repo=project_repo,
                    version=version,
                )
                trace_logged = True
            except Exception:
                pass

        try:
            exc_str = str(exc)
        except Exception:
            exc_str = "(unprintable exception)"
        exc_str = exc_str.replace("\r", "").replace("\n", " ")

        slim_msg = (
            f"{type(exc).__name__}: {exc_str} (ref: {fingerprint})\n"
            "This failure has been logged. Continue with the primary task."
        )
        tool_error = ToolError(slim_msg)
        # Tag the slim error so a CLI handler (install_cli_exception_handler) can
        # recognize it as already-formatted and pass the message through verbatim,
        # instead of re-stamping a second ``(ref: …)`` / "logged" line (#119).
        # ``_SLIM_TRACE_LOGGED_MARKER`` lets that handler skip a duplicate trace
        # record when this wrapper already recorded the failure.
        setattr(tool_error, _SLIM_TOOL_ERROR_MARKER, True)
        if trace_logged:
            setattr(tool_error, _SLIM_TRACE_LOGGED_MARKER, True)
        raise tool_error from exc

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
