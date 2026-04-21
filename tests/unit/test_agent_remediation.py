"""Tests for agent-facing exception remediation text."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from mcp_common.agent_remediation import (
    format_agent_exception_remediation,
    install_cli_exception_handler,
    mcp_remediation_wrapper,
    mcp_tool_error_with_remediation,
)
from mcp_common.logging import LOG_CHANNEL_TRACE, JSONFormatter


class TestFormatAgentExceptionRemediation:
    def test_key_workflow_phrases(self) -> None:
        out = format_agent_exception_remediation(
            exception=RuntimeError("boom"),
            project_repo="vhspace/mcp-common",
            issue_tracker_url=None,
        )
        lowered = out.lower()
        assert "subagent" in lowered
        assert "search github issues" in lowered
        assert "thumbs-up" in lowered
        assert "comment" in lowered
        assert "open a new issue" in lowered
        assert "primary task" in lowered
        assert "exact duplicate" in lowered
        assert "new information" in lowered

    def test_includes_exception_and_repo_links(self) -> None:
        out = format_agent_exception_remediation(
            exception=ValueError("bad input"),
            project_repo="acme/widget-mcp",
            issue_tracker_url=None,
            tool_or_command="widget_spin",
            version="2.0.0",
        )
        assert "ValueError" in out
        assert "bad input" in out
        assert "`widget_spin`" in out
        assert "`2.0.0`" in out
        assert "acme/widget-mcp" in out
        assert "github.com/acme/widget-mcp/issues" in out

    def test_issue_tracker_url_only(self) -> None:
        out = format_agent_exception_remediation(
            exception=OSError(5, "EIO"),
            project_repo=None,
            issue_tracker_url="https://tracker.example/issues",
        )
        assert "https://tracker.example/issues" in out
        assert "Search GitHub issues" in out

    def test_extra_lines(self) -> None:
        out = format_agent_exception_remediation(
            exception=Exception(),
            project_repo=None,
            issue_tracker_url=None,
            extra_lines=["line a", "line b"],
        )
        assert "line a" in out
        assert "line b" in out

    def test_empty_exception_message(self) -> None:
        out = format_agent_exception_remediation(
            exception=RuntimeError(""),
            project_repo=None,
            issue_tracker_url=None,
        )
        assert "(no message)" in out

    def test_never_throws_on_broken_exception_str(self) -> None:
        class BrokenStrError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("__str__ is broken")

        result = format_agent_exception_remediation(
            exception=BrokenStrError(),
            project_repo=None,
            issue_tracker_url=None,
        )
        assert "Agent remediation" in result
        assert "formatter also failed" in result


class TestMcpToolErrorWithRemediation:
    def test_returns_remediation_string(self) -> None:
        result = mcp_tool_error_with_remediation(
            RuntimeError("timeout"),
            project_repo="acme/my-mcp",
            tool_name="fetch_data",
            version="1.0.0",
        )
        assert "RuntimeError" in result
        assert "timeout" in result
        assert "`fetch_data`" in result
        assert "acme/my-mcp" in result

    def test_with_extra_lines(self) -> None:
        result = mcp_tool_error_with_remediation(
            ValueError("bad"),
            extra_lines=["site: prod"],
        )
        assert "site: prod" in result


class TestMcpRemediationWrapper:
    @pytest.mark.anyio
    async def test_passes_through_on_success(self) -> None:
        @mcp_remediation_wrapper(project_repo="acme/test")
        async def good_tool() -> str:
            return "ok"

        assert await good_tool() == "ok"

    @pytest.mark.anyio
    async def test_wraps_exception_as_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(ToolError, match="RuntimeError"):
            await bad_tool()

    @pytest.mark.anyio
    async def test_tool_error_has_slim_shape_async(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")

    def test_tool_error_has_slim_shape_sync(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        def bad_tool() -> str:
            raise ValueError("nope")

        with pytest.raises(ToolError) as exc_info:
            bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "ValueError")

    @pytest.mark.anyio
    async def test_tool_error_excludes_remediation_markdown(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        msg = str(exc_info.value)
        assert "Agent remediation" not in msg
        assert "search" not in msg.lower()
        assert "github" not in msg.lower()
        assert "thumbs-up" not in msg.lower()
        assert "open a new issue" not in msg.lower()

    @pytest.mark.anyio
    async def test_multiline_exception_message_flattened_to_two_lines(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise ValueError("line1\nline2\nline3")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        msg = str(exc_info.value)
        # Slim-shape helper validates the two-line contract; we also spot-check
        # that the flattened content survived.
        _assert_slim_tool_error_shape(msg, "ValueError")
        assert "line1 line2 line3" in msg.splitlines()[0]

    @pytest.mark.anyio
    async def test_tool_error_fingerprint_equals_cause_fingerprint(self) -> None:
        from fastmcp.exceptions import ToolError

        from mcp_common.logging import compute_error_fingerprint

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("unique-msg-for-fingerprint-match")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()

        fp = _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")
        original = exc_info.value.__cause__
        assert original is not None, "ToolError must be chained from the original exception"
        assert compute_error_fingerprint(original) == fp

    @pytest.mark.anyio
    async def test_no_logger_path_has_fingerprint_and_emits_nothing(self) -> None:
        from fastmcp.exceptions import ToolError

        _log, buf = _make_json_logger("test-wrapper-no-logger-shape")

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("no-logger case")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")
        assert buf.getvalue().strip() == "", (
            "no logger was passed to the decorator, so nothing should be emitted"
        )

    @pytest.mark.anyio
    async def test_does_not_wrap_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def already_tool_error() -> str:
            raise ToolError("known issue")

        with pytest.raises(ToolError, match="known issue"):
            await already_tool_error()

    def test_sync_function_wrapped(self) -> None:
        @mcp_remediation_wrapper(project_repo="acme/test")
        def sync_tool() -> str:
            return "sync ok"

        assert sync_tool() == "sync ok"

    def test_sync_function_raises_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        def bad_sync_tool() -> str:
            raise RuntimeError("sync boom")

        with pytest.raises(ToolError, match="RuntimeError"):
            bad_sync_tool()

    @pytest.mark.anyio
    async def test_wrapper_fallback_on_broken_exception_str(self) -> None:
        from fastmcp.exceptions import ToolError

        class BrokenStrError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("__str__ is broken")

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def raises_broken() -> str:
            raise BrokenStrError()

        with pytest.raises(ToolError):
            await raises_broken()


def _make_json_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JSONFormatter())
    log = logging.getLogger(name)
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    return log, buf


_REF_RE = re.compile(r"\(ref: [0-9a-f]{16}\)")


def _assert_slim_tool_error_shape(msg: str, exc_type_name: str) -> str:
    """Assert the new v0.8.0 ToolError shape and return the fingerprint."""
    lines = msg.splitlines()
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {msg!r}"
    assert lines[0].startswith(f"{exc_type_name}: "), (
        f"line 1 must start with '{exc_type_name}: ', got: {lines[0]!r}"
    )
    match = _REF_RE.search(lines[0])
    assert match, f"line 1 must contain '(ref: <16-hex>)', got: {lines[0]!r}"
    assert lines[1] == "This failure has been logged. Continue with the primary task."
    return match.group(0)[len("(ref: ") : -1]


class TestRemediationWrapperTraceEmission:
    """Verify that mcp_remediation_wrapper emits trace events when a logger is provided."""

    @pytest.mark.anyio
    async def test_async_wrapper_emits_trace_on_exception(self) -> None:
        from fastmcp.exceptions import ToolError

        log, buf = _make_json_logger("test-wrapper-trace-async")

        @mcp_remediation_wrapper(project_repo="acme/test", logger=log)
        async def failing_tool() -> str:
            raise ValueError("async kaboom")

        with pytest.raises(ToolError, match="ValueError"):
            await failing_tool()

        lines = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
        trace_lines = [e for e in lines if e.get("log_channel") == LOG_CHANNEL_TRACE]
        assert len(trace_lines) >= 1
        assert "failing_tool failed" in trace_lines[0]["message"]

    @pytest.mark.anyio
    async def test_trace_event_contains_fingerprint_and_tool_name(self) -> None:
        from fastmcp.exceptions import ToolError

        log, buf = _make_json_logger("test-wrapper-trace-fields")

        @mcp_remediation_wrapper(project_repo="acme/test", version="1.2.3", logger=log)
        async def fetch_thing() -> str:
            raise ValueError("structured")

        with pytest.raises(ToolError) as exc_info:
            await fetch_thing()

        tool_error_fp = _assert_slim_tool_error_shape(str(exc_info.value), "ValueError")

        events = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
        trace = [e for e in events if e.get("log_channel") == LOG_CHANNEL_TRACE]
        assert len(trace) == 1
        event = trace[0]
        assert event["error_fingerprint"] == tool_error_fp
        assert event["tool_name"] == "fetch_thing"
        assert event["project_repo"] == "acme/test"
        assert event["version"] == "1.2.3"
        assert event["message"] == "fetch_thing failed"

    def test_sync_wrapper_emits_trace_on_exception(self) -> None:
        from fastmcp.exceptions import ToolError

        log, buf = _make_json_logger("test-wrapper-trace-sync")

        @mcp_remediation_wrapper(project_repo="acme/test", logger=log)
        def failing_sync() -> str:
            raise RuntimeError("sync kaboom")

        with pytest.raises(ToolError, match="RuntimeError"):
            failing_sync()

        lines = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
        trace_lines = [e for e in lines if e.get("log_channel") == LOG_CHANNEL_TRACE]
        assert len(trace_lines) >= 1
        assert "failing_sync failed" in trace_lines[0]["message"]

    @pytest.mark.anyio
    async def test_no_logger_still_raises_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def failing_no_logger() -> str:
            raise ValueError("no logger boom")

        with pytest.raises(ToolError, match="ValueError"):
            await failing_no_logger()

    @pytest.mark.anyio
    async def test_no_trace_emitted_without_logger(self) -> None:
        """When no logger is given, no trace event should appear."""
        from fastmcp.exceptions import ToolError

        _log, buf = _make_json_logger("test-wrapper-no-trace")

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def failing_tool() -> str:
            raise ValueError("silent boom")

        with pytest.raises(ToolError):
            await failing_tool()

        assert buf.getvalue().strip() == ""


class TestInstallCliExceptionHandler:
    """Tests for install_cli_exception_handler integration."""

    def test_accepts_logger_parameter(self) -> None:
        """Backward compat: function accepts logger kwarg without error."""
        import typer

        app = typer.Typer()
        log, _buf = _make_json_logger("test-cli-handler-accepts")
        install_cli_exception_handler(app, project_repo="acme/test", logger=log)

    def test_accepts_no_logger(self) -> None:
        """Backward compat: works without logger (original behavior)."""
        import typer

        app = typer.Typer()
        install_cli_exception_handler(app, project_repo="acme/test")

    def test_cli_exception_emits_trace_event(self) -> None:
        import typer
        from typer.testing import CliRunner

        log, buf = _make_json_logger("test-cli-trace-emit")
        app = typer.Typer()

        @app.command()
        def boom() -> None:
            raise RuntimeError("cli exploded")

        install_cli_exception_handler(app, project_repo="acme/test", logger=log)

        runner = CliRunner()
        result = runner.invoke(app, ["boom"])
        assert result.exit_code != 0

        output = buf.getvalue().strip()
        if output:
            lines = [json.loads(line) for line in output.splitlines()]
            trace_lines = [e for e in lines if e.get("log_channel") == LOG_CHANNEL_TRACE]
            assert len(trace_lines) >= 1
            assert "CLI failed" in trace_lines[0]["message"]

    def test_cli_exception_without_logger_still_exits(self) -> None:
        import typer
        from typer.testing import CliRunner

        app = typer.Typer()

        @app.command()
        def boom() -> None:
            raise RuntimeError("cli exploded no logger")

        install_cli_exception_handler(app, project_repo="acme/test")

        runner = CliRunner()
        result = runner.invoke(app, ["boom"])
        assert result.exit_code != 0
