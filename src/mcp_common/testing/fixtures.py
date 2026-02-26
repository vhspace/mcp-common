"""Shared pytest fixtures for MCP server testing."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastmcp import Client, FastMCP


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio backend for anyio tests."""
    return "asyncio"


async def mcp_client(server: FastMCP) -> AsyncGenerator[Client[Any], None]:
    """Create an MCP client connected to a FastMCP server instance.

    Usage in conftest.py::

        from mcp_common.testing import mcp_client
        from my_server import mcp as app

        @pytest.fixture
        async def client():
            async for c in mcp_client(app):
                yield c
    """
    async with Client(server) as client:
        yield client
