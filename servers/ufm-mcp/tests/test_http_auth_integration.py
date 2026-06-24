"""Integration tests: full HTTP client-server auth verification.

Starts a real uvicorn server with auth enabled and verifies that
tool calls are blocked without a valid token and allowed with one.
"""

import asyncio
import os

import httpx
import pytest
import uvicorn
from fastmcp import Client

pytest_plugins = ["anyio"]

TOKEN = "test-secret-token"

MOCK_ENV = {
    "MCP_HTTP_ACCESS_TOKEN": TOKEN,
    "UFM_URL": "http://fake-ufm.local",
    "UFM_TOKEN": "fake-ufm-token",
}


@pytest.fixture
async def live_server():
    """Start the MCP server with auth enabled on a random port."""
    for key, val in MOCK_ENV.items():
        os.environ[key] = val

    from ufm_mcp.server import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)

    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp", TOKEN

    server.should_exit = True
    await task
    for key in MOCK_ENV:
        os.environ.pop(key, None)


@pytest.mark.anyio
async def test_valid_token_can_list_tools(live_server):
    """With the correct Bearer token the client can list tools."""
    url, token = live_server
    async with Client(url, auth=token) as client:
        tools = await client.list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0


@pytest.mark.anyio
async def test_invalid_token_is_rejected(live_server):
    """An incorrect token must be rejected by the auth middleware."""
    url, _ = live_server
    with pytest.raises(httpx.HTTPStatusError, match="401"):
        async with Client(url, auth="wrong-token") as client:
            await client.list_tools()


@pytest.mark.anyio
async def test_no_token_is_rejected(live_server):
    """Omitting the token entirely must be rejected."""
    url, _ = live_server
    with pytest.raises(httpx.HTTPStatusError, match="401"):
        async with Client(url) as client:
            await client.list_tools()
