"""Tests for agent-facing exception remediation text."""

from __future__ import annotations

import io
import json
import logging

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
