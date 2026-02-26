"""MCP-specific test assertions."""

from __future__ import annotations

from typing import Any

from fastmcp import Client


async def assert_tool_exists(client: Client[Any], tool_name: str) -> None:
    """Assert that a tool is registered on the server."""
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert tool_name in names, f"Tool '{tool_name}' not found. Available: {names}"


async def assert_tool_success(
    client: Client[Any],
    tool_name: str,
    arguments: dict[str, object],
) -> object:
    """Call a tool and assert it returns successfully (no error).

    Returns the result data for further assertions.
    """
    result = await client.call_tool(tool_name, arguments)
    return result
