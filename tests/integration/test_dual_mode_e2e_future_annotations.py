"""PEP 563 regression: dual-mode parity under ``from __future__ import annotations``.

Mirrors :mod:`tests.integration.test_dual_mode_e2e` but with PEP 563
stringified annotations enabled at the top of the module. ``fastmcp.tool()``
caches a synthesized signature on ``fn.__signature__`` at decoration time;
once that cache exists, ``inspect.signature(fn, eval_str=True)`` returns the
cached signature *without* re-evaluating its strings, so any annotation that
relied on PEP 563 deferral comes back as a :class:`typing.ForwardRef`. The
framework now resolves annotations via :func:`typing.get_type_hints` instead,
which always re-evaluates against ``fn.__globals__`` regardless of the cached
signature state. This file is the executable proof.
"""

from __future__ import annotations

import json

import anyio
import pydantic
import pytest
from fastmcp import Client, Context, FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.testing.dual_mode import make_cli_runner

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
    return make_cli_runner()


def _call_mcp_tool(server: FastMCP, tool_name: str, args: dict) -> dict:
    """Invoke ``tool_name`` over the in-memory FastMCP client."""

    async def _run() -> dict:
        async with Client(server) as client:
            result = await client.call_tool(tool_name, args)
            payload = result.structured_content or {}
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
    """Same function, two surfaces, same JSON output — under PEP 563."""

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
        """Context-using tool returns the same payload via both surfaces."""
        mcp_result = _call_mcp_tool(
            mcp_with_tools, "lookup_with_progress", {"hostname": "sw01.dc1"}
        )
        app = build_cli_from_mcp(mcp_with_tools, project_repo="togethercomputer/netbox-mcp")
        cli_result = _call_cli(app, runner, ["lookup-with-progress", "--hostname", "sw01.dc1"])

        assert mcp_result == cli_result
        assert mcp_result == _DEVICE_FIXTURE.model_dump()
