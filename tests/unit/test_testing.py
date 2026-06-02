"""Tests for the testing utilities module."""

import pytest
from fastmcp import Client, FastMCP

from mcp_common.testing import assert_tool_exists, assert_tool_success, mcp_client


def _make_test_server() -> FastMCP:
    server = FastMCP("test-server")

    @server.tool()
    def echo(message: str) -> str:
        """Echo a message back."""
        return f"echo: {message}"

    return server


class TestMCPClient:
    @pytest.mark.anyio
    async def test_yields_connected_client(self) -> None:
        server = _make_test_server()
        async for client in mcp_client(server):
            assert isinstance(client, Client)
            tools = await client.list_tools()
            assert len(tools) >= 1


class TestAssertToolExists:
    @pytest.mark.anyio
    async def test_passes_for_registered_tool(self) -> None:
        server = _make_test_server()
        async for client in mcp_client(server):
            await assert_tool_exists(client, "echo")

    @pytest.mark.anyio
    async def test_fails_for_missing_tool(self) -> None:
        server = _make_test_server()
        async for client in mcp_client(server):
            with pytest.raises(AssertionError, match="not_a_tool"):
                await assert_tool_exists(client, "not_a_tool")


class TestAssertToolSuccess:
    @pytest.mark.anyio
    async def test_returns_result(self) -> None:
        server = _make_test_server()
        async for client in mcp_client(server):
            result = await assert_tool_success(client, "echo", {"message": "hi"})
            assert result is not None
