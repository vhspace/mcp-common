"""Tests for ``build_cli_from_mcp`` — the materializer side of dual_mode."""

import json
from typing import Literal

import pydantic
import pytest
from fastmcp import Context, FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.dual_mode._typer_params import PYDANTIC_FLATTEN_THRESHOLD


@pytest.fixture
def mcp() -> FastMCP:
    instance = FastMCP("netbox")
    yield instance
    _clear(instance)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


# Module-level Pydantic models so PEP 563 forward refs resolve cleanly.
class _SmallPayload(pydantic.BaseModel):
    name: str
    count: int = 0


_BigPayload = pydantic.create_model(
    "_BigPayload",
    **{f"f{i}": (str, ...) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)},  # type: ignore[arg-type]
)


class TestSyncToolEndToEnd:
    def test_sync_tool_json_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == {"hostname": "sw01", "interfaces": False}

    def test_sync_tool_human_mode_uses_str(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def echo(message: str) -> str:
            """Echo a message."""
            return f"Echo: {message}"

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["echo", "--message", "hi"])

        assert result.exit_code == 0
        assert "Echo: hi" in result.stdout

    def test_sync_tool_bool_flag_activation(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(
            app,
            ["lookup-device", "--hostname", "sw01", "--include-interfaces", "--json"],
        )

        parsed = json.loads(result.stdout)
        assert parsed["interfaces"] is True


class TestAsyncToolEndToEnd:
    def test_async_tool_is_driven_by_asyncio_run(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def async_lookup(name: str) -> dict:
            """Async lookup."""
            return {"name": name, "kind": "async"}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["async-lookup", "--name", "alice", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "alice", "kind": "async"}

    def test_async_exception_propagates(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def boom(_x: int) -> dict:
            """Always raises."""
            raise RuntimeError("explode")

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["boom", "--x", "1"])
        assert result.exit_code != 0


class TestContextShimming:
    def test_context_tool_runs_without_session(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def with_context(ctx: Context, name: str) -> dict:
            """Tool that uses the Context API."""
            await ctx.info(f"looking up {name}")
            await ctx.report_progress(progress=50, total=100, message="halfway")
            return {"name": name, "had_context": True}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["with-context", "--name", "bob", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "bob", "had_context": True}
        # Progress should land on stderr (default CliContext stream).
        assert "halfway" in result.stderr


class TestPydanticParameter:
    def test_small_model_is_flattened_on_cli(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create_device(payload: _SmallPayload) -> dict:
            """Create a device."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(
            app,
            [
                "create-device",
                "--payload-name",
                "sw01",
                "--payload-count",
                "5",
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "sw01", "count": 5}

    def test_large_model_uses_params_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create_big(payload: _BigPayload) -> dict:  # type: ignore[valid-type]
            """Create something with many fields."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        blob = json.dumps({f"f{i}": str(i) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)})
        result = runner.invoke(
            app,
            ["create-big", "--payload-params", blob, "--json"],
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed[f"f{PYDANTIC_FLATTEN_THRESHOLD}"] == str(PYDANTIC_FLATTEN_THRESHOLD)


class TestLiteralChoice:
    def test_literal_choice_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def select(mode: Literal["fast", "slow"]) -> dict:
            """Pick a mode."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "fast", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"mode": "fast"}

    def test_literal_choice_rejects_invalid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def select(mode: Literal["fast", "slow"]) -> dict:
            """Pick a mode."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "bogus"])

        assert result.exit_code != 0
        assert "fast" in result.stderr or "slow" in result.stderr


class TestListParameter:
    def test_multi_value_list_collected(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def tag_all(tags: list[str]) -> dict:
            """Tag many things."""
            return {"tags": tags}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["tag-all", "--tags", "alpha", "--tags", "beta", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"tags": ["alpha", "beta"]}


class TestOptionalParameter:
    def test_optional_with_default_is_optional(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup(name: str | None = None) -> dict:
            """Optional name lookup."""
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": None}


class TestCliGroup:
    def test_cli_group_creates_subcommand(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            """Look up under devices/"""
            return {"hostname": hostname}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")

        result = runner.invoke(app, ["devices", "--help"])
        assert result.exit_code == 0
        assert "lookup-device" in result.stdout

        result = runner.invoke(app, ["devices", "lookup-device", "--hostname", "sw01", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"hostname": "sw01"}

    def test_multiple_commands_share_one_subgroup(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            return {"hostname": hostname}

        @dual_mode_tool(mcp, cli_group="devices")
        def list_devices() -> dict:
            return {"devices": []}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["devices", "--help"])
        assert "lookup-device" in result.stdout
        assert "list-devices" in result.stdout


class TestMcpOnly:
    def test_mcp_only_tools_omitted_from_cli(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def public_tool(x: int) -> int:
            """Public tool."""
            return x

        @dual_mode_tool(mcp, mcp_only=True)
        def server_only(x: int) -> int:
            """MCP-only tool."""
            return x

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["--help"])
        assert "public-tool" in result.stdout
        assert "server-only" not in result.stdout


class TestCustomFormatter:
    def test_human_formatter_used_in_human_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        def fmt_dict(d: dict) -> str:
            return f"NAME={d['name']}"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            """Return a dict."""
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--name", "sw01"])

        assert result.exit_code == 0
        assert "NAME=sw01" in result.stdout

    def test_human_formatter_ignored_in_json_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        def fmt_dict(d: dict) -> str:
            return "HUMAN ONLY"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--name", "sw01", "--json"])

        assert result.exit_code == 0
        assert "HUMAN ONLY" not in result.stdout
        assert json.loads(result.stdout) == {"name": "sw01"}


class TestExceptionPropagation:
    def test_sync_exception_bubbles_to_exit_handler(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def boom(x: int) -> int:
            """Always raises."""
            raise RuntimeError("kaboom")

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        # CliRunner catches exceptions by default; we just verify the command exited non-zero.
        result = runner.invoke(app, ["boom", "--x", "1"])
        assert result.exit_code != 0


class TestAppNaming:
    def test_default_cli_name_strips_mcp_suffix(self) -> None:
        mcp = FastMCP("netbox-mcp")
        try:

            @dual_mode_tool(mcp)
            def lookup(x: int) -> int:
                return x

            app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
            assert app.info.name == "netbox-cli"
        finally:
            _clear(mcp)

    def test_explicit_name_wins(self) -> None:
        mcp = FastMCP("netbox-mcp")
        try:

            @dual_mode_tool(mcp)
            def lookup(x: int) -> int:
                return x

            app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp", name="my-custom-cli")
            assert app.info.name == "my-custom-cli"
        finally:
            _clear(mcp)


class TestEmptyRegistry:
    def test_no_tools_yields_help_only_app(self, runner: CliRunner) -> None:
        mcp = FastMCP("empty")
        try:
            app = build_cli_from_mcp(mcp, project_repo="vhspace/empty-mcp")
            result = runner.invoke(app, ["--help"])
            assert result.exit_code == 0
        finally:
            _clear(mcp)
