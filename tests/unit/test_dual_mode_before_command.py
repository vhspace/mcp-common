"""Tests for ``build_cli_from_mcp(..., before_command=...)`` — issue #103.

``before_command`` is a CLI-time setup hook (instantiate the REST client,
validate env) that must run once per real command invocation, AFTER Typer
parses args but BEFORE the synthesized tool function runs — and must be
skipped on every introspection-only path (``--help`` at any level, or a bare
invocation with no subcommand) so ``<cli> --help`` works without credentials.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.testing.dual_mode import make_cli_runner


@pytest.fixture
def mcp() -> FastMCP:
    instance = FastMCP("netbox")
    yield instance
    _clear(instance)


@pytest.fixture
def runner() -> CliRunner:
    return make_cli_runner()


class TestBeforeCommandInvocation:
    def test_hook_called_exactly_once_on_invocation(self, mcp: FastMCP, runner: CliRunner) -> None:
        calls: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: calls.append("init"),
        )
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert calls == ["init"]

    def test_hook_runs_before_tool_body(self, mcp: FastMCP, runner: CliRunner) -> None:
        order: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            order.append("tool")
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: order.append("before"),
        )
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert order == ["before", "tool"]

    def test_hook_called_for_subgroup_command(self, mcp: FastMCP, runner: CliRunner) -> None:
        calls: list[str] = []

        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname under devices/."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: calls.append("init"),
        )
        result = runner.invoke(app, ["devices", "lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert calls == ["init"]


class TestBeforeCommandSkippedOnHelp:
    def test_hook_not_called_on_top_level_help(self, mcp: FastMCP, runner: CliRunner) -> None:
        calls: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: calls.append("init"),
        )
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert calls == []
        assert "lookup-device" in result.stdout

    def test_hook_not_called_on_subcommand_help(self, mcp: FastMCP, runner: CliRunner) -> None:
        """`<cli> <cmd> --help` must not require credentials either."""
        calls: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: calls.append("init"),
        )
        result = runner.invoke(app, ["lookup-device", "--help"])

        assert result.exit_code == 0
        assert calls == []

    def test_hook_not_called_on_bare_app(self, mcp: FastMCP, runner: CliRunner) -> None:
        """No subcommand → no_args_is_help prints help; hook must not fire."""
        calls: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=lambda: calls.append("init"),
        )
        runner.invoke(app, [])

        assert calls == []


class TestBeforeCommandExceptionHandling:
    def test_hook_exception_yields_nonzero_exit(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname}

        def boom() -> None:
            raise RuntimeError("NETBOX_URL and NETBOX_TOKEN env vars required")

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=boom,
        )
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01"])

        # The exception propagates like a tool error: non-zero exit, surfaced
        # via result.exception (CliRunner bypasses the install_cli_exception_handler
        # footer — see build_cli_from_mcp docstring), never the tool body output.
        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        assert "env vars required" in str(result.exception)

    def test_hook_exception_skips_tool_body(self, mcp: FastMCP, runner: CliRunner) -> None:
        ran: list[str] = []

        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname."""
            ran.append("tool")
            return {"hostname": hostname}

        def boom() -> None:
            raise RuntimeError("init failed")

        app = build_cli_from_mcp(
            mcp,
            project_repo="togethercomputer/netbox-mcp",
            before_command=boom,
        )
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01"])

        assert result.exit_code != 0
        assert ran == []  # tool body never reached


class TestBeforeCommandRegression:
    def test_no_hook_behaves_as_before(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        # No before_command passed: identical to pre-#103 behavior.
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0
        import json

        assert json.loads(result.stdout) == {"hostname": "sw01", "interfaces": False}
