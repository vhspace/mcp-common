"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastmcp import Client
from mcp_common.testing import mcp_client

from mcp_network.server import mcp as app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[Client, None]:
    async for c in mcp_client(app):
        yield c
