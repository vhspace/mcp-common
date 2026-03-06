"""Integration tests: full HTTP client-server auth verification via live uvicorn.

Starts a real uvicorn server with bearer-token auth enabled and verifies that
FastMCP Client tool calls are blocked without auth and allowed with valid auth.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import uvicorn
from fastmcp import Client, FastMCP
from httpx import HTTPStatusError

from mcp_common.http import add_health_route, create_http_app

pytest_plugins = ["anyio"]

TOKEN = "test-secret-token"


def _build_app(auth_token: str | None = TOKEN) -> object:
    """Create a minimal FastMCP app with auth via mcp-common utilities."""
    mcp = FastMCP("test-server")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo a message back."""
        return message

    add_health_route(mcp, "test-server")
    return create_http_app(mcp, auth_token=auth_token)


@pytest.fixture
async def live_server():
    """Start the MCP server on a random port with auth enabled."""
    app = _build_app(auth_token=TOKEN)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", TOKEN
    server.should_exit = True
    await task


@pytest.mark.anyio
async def test_tool_call_with_valid_token(live_server):
    """Tool calls succeed with a valid Bearer token."""
    base_url, token = live_server
    async with Client(f"{base_url}/mcp", auth=token) as client:
        tools = await client.list_tools()
        assert len(tools) > 0
        assert any(t.name == "echo" for t in tools)


@pytest.mark.anyio
async def test_tool_call_rejected_without_token(live_server):
    """Tool calls are rejected without authentication."""
    base_url, _ = live_server
    with pytest.raises(HTTPStatusError, match="401"):
        async with Client(f"{base_url}/mcp") as client:
            await client.list_tools()


@pytest.mark.anyio
async def test_tool_call_rejected_with_wrong_token(live_server):
    """Tool calls are rejected with an invalid token."""
    base_url, _ = live_server
    with pytest.raises(HTTPStatusError, match="401"):
        async with Client(f"{base_url}/mcp", auth="wrong-token") as client:
            await client.list_tools()


@pytest.mark.anyio
async def test_health_accessible_without_auth(live_server):
    """The /health endpoint is accessible without authentication."""
    base_url, _ = live_server
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base_url}/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "test-server"
