"""Shared fixtures for netbox-mcp tests."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
