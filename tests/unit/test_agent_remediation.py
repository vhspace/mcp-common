"""Tests for agent-facing exception remediation text."""

import pytest

from mcp_common.agent_remediation import (
    format_agent_exception_remediation,
    mcp_remediation_wrapper,
    mcp_tool_error_with_remediation,
)


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
        class BrokenStr(Exception):
            def __str__(self) -> str:
                raise RuntimeError("__str__ is broken")

        result = format_agent_exception_remediation(
            exception=BrokenStr(),
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

    @pytest.mark.anyio
    async def test_sync_function_wrapped(self) -> None:
        @mcp_remediation_wrapper(project_repo="acme/test")
        def sync_tool() -> str:
            return "sync ok"

        assert await sync_tool() == "sync ok"

    @pytest.mark.anyio
    async def test_wrapper_fallback_on_broken_exception_str(self) -> None:
        from fastmcp.exceptions import ToolError

        class BrokenStr(Exception):
            def __str__(self) -> str:
                raise RuntimeError("__str__ is broken")

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def raises_broken() -> str:
            raise BrokenStr()

        with pytest.raises(ToolError):
            await raises_broken()
