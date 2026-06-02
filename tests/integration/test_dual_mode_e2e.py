"""End-to-end integration test: same function as MCP tool + CLI command.

The acceptance criterion from togethercomputer/mcp-common#86 is that a single
``@dual_mode_tool``-decorated function is callable via both the FastMCP
in-memory client *and* :class:`typer.testing.CliRunner`, with consistent
results in ``--json`` mode. This file is the executable form of that
contract.
"""

import json

import anyio
import pydantic
import pytest
from fastmcp import Client, Context, FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear

pytestmark = pytest.mark.integration


class _Device(pydantic.BaseModel):
    """Pydantic return type so the parity check also covers serialization."""

    hostname: str
    oob_ip: str
    primary_ip: str
    site: str


_DEVICE_FIXTURE = _Device(
    hostname="sw01.dc1",
    oob_ip="192.168.1.10",
    primary_ip="10.0.1.10",
    site="dc1",
)


@pytest.fixture
def mcp_with_tools() -> FastMCP:
    """A FastMCP instance carrying a mixed sync/async/Context tool set."""
    instance = FastMCP("netbox")

    @dual_mode_tool(instance)
    def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
        """Resolve a hostname/IP to a NetBox device (sync)."""
        return {
            "hostname": hostname,
            "include_interfaces": include_interfaces,
            "found": True,
        }

    @dual_mode_tool(instance)
    async def search_devices(query: str, limit: int = 10) -> dict:
        """Search for devices by query (async)."""
        return {"query": query, "limit": limit, "results": [_DEVICE_FIXTURE.model_dump()]}

    @dual_mode_tool(instance)
    async def lookup_with_progress(ctx: Context, hostname: str) -> dict:
        """Lookup with Context-driven progress (async + Context)."""
        await ctx.info(f"Resolving {hostname}")
        await ctx.report_progress(progress=50, total=100, message="halfway")
        await ctx.report_progress(progress=100, total=100, message="done")
        return _DEVICE_FIXTURE.model_dump()

    yield instance
    _clear(instance)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _call_mcp_tool(server: FastMCP, tool_name: str, args: dict) -> dict:
    """Invoke ``tool_name`` over the in-memory FastMCP client.

    Returns the parsed JSON content of the tool result so callers can
    diff the dict against the CLI's ``--json`` output.
    """

    async def _run() -> dict:
        async with Client(server) as client:
            result = await client.call_tool(tool_name, args)
            payload = result.structured_content or {}
            # FastMCP wraps single-value returns under {"result": <value>}; if
            # the tool returned a dict already, payload IS that dict.
            if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
                return payload["result"]
            return payload

    return anyio.run(_run)


def _call_cli(app, runner: CliRunner, argv: list[str]) -> dict:
    """Invoke ``app`` via ``CliRunner`` and parse the ``--json`` payload."""
    result = runner.invoke(app, [*argv, "--json"])
    assert result.exit_code == 0, f"CLI failed: {result.stdout} / {result.stderr}"
    return json.loads(result.stdout)


class TestParity:
    """Same function, two surfaces, same JSON output."""

    def test_sync_tool_parity(self, mcp_with_tools: FastMCP, runner: CliRunner) -> None:
        mcp_result = _call_mcp_tool(mcp_with_tools, "lookup_device", {"hostname": "sw01"})
        app = build_cli_from_mcp(mcp_with_tools, project_repo="togethercomputer/netbox-mcp")
        cli_result = _call_cli(app, runner, ["lookup-device", "--hostname", "sw01"])

        assert mcp_result == cli_result
        assert mcp_result == {
            "hostname": "sw01",
            "include_interfaces": False,
            "found": True,
        }

    def test_async_tool_parity(self, mcp_with_tools: FastMCP, runner: CliRunner) -> None:
        mcp_result = _call_mcp_tool(
            mcp_with_tools, "search_devices", {"query": "rack-1", "limit": 5}
        )
        app = build_cli_from_mcp(mcp_with_tools, project_repo="togethercomputer/netbox-mcp")
        cli_result = _call_cli(app, runner, ["search-devices", "--query", "rack-1", "--limit", "5"])

        assert mcp_result == cli_result

    def test_context_tool_parity(self, mcp_with_tools: FastMCP, runner: CliRunner) -> None:
        """Context-using tool returns the same payload via both surfaces.

        The CLI side shims Context with :class:`CliContext`; the MCP side
        uses the real Context. Both invocations must produce the same
        return value — that's the parity the framework is designed to
        guarantee.
        """
        mcp_result = _call_mcp_tool(
            mcp_with_tools, "lookup_with_progress", {"hostname": "sw01.dc1"}
        )
        app = build_cli_from_mcp(mcp_with_tools, project_repo="togethercomputer/netbox-mcp")
        cli_result = _call_cli(app, runner, ["lookup-with-progress", "--hostname", "sw01.dc1"])

        assert mcp_result == cli_result
        assert mcp_result == _DEVICE_FIXTURE.model_dump()


class TestMcpToolRegistration:
    """Independently confirm the FastMCP side stays registered correctly."""

    def test_all_dual_mode_tools_are_visible_to_mcp_client(self, mcp_with_tools: FastMCP) -> None:
        async def _list() -> list[str]:
            async with Client(mcp_with_tools) as client:
                tools = await client.list_tools()
                return sorted(t.name for t in tools)

        names = anyio.run(_list)
        assert names == sorted(["lookup_device", "search_devices", "lookup_with_progress"])

    def test_async_tool_callable_via_mcp(self, mcp_with_tools: FastMCP) -> None:
        result = _call_mcp_tool(mcp_with_tools, "search_devices", {"query": "x"})
        assert result["query"] == "x"
