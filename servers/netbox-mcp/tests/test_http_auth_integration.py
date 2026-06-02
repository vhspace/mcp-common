"""Integration tests: full HTTP client-server auth verification via live uvicorn.

Starts a real uvicorn server with bearer-token auth enabled and verifies that
FastMCP Client tool calls are blocked without auth and allowed with valid auth.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastmcp import Client
from httpx import HTTPStatusError

from netbox_mcp.server import create_app

pytest_plugins = ["anyio"]

TOKEN = "test-secret-token"


@pytest.fixture
def _reset_init():
    """Reset the module-level _initialized flag and middleware between tests."""
    import netbox_mcp.server as mod

    original_init = mod._initialized
    original_middleware = mod.mcp.middleware[:]
    mod._initialized = False
    yield
    mod._initialized = original_init
    mod.mcp.middleware[:] = original_middleware


def _make_app(access_token: str | None = TOKEN):
    """Build a test ASGI app via create_app() with mocked NetBox client."""
    import netbox_mcp.server as mod

    mod._initialized = False
    env = {
        "NETBOX_URL": "https://netbox.test/",
        "NETBOX_TOKEN": "fake-token",
        "TRANSPORT": "http",
    }
    if access_token is not None:
        env["MCP_HTTP_ACCESS_TOKEN"] = access_token
    with (
        patch.dict("os.environ", env, clear=False),
        patch("netbox_mcp.server.NetBoxRestClient"),
    ):
        app = create_app()
    return app


@pytest.fixture
async def live_server(_reset_init):
    """Start the MCP server on a random port with auth enabled."""
    app = _make_app(access_token=TOKEN)
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
        assert body["service"] == "netbox-mcp"
