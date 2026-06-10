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
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Call a tool and assert it does not raise.

    FastMCP's Client.call_tool raises on server errors, so a successful
    return means the tool executed without error.

    Returns the result for further assertions.
    """
    result = await client.call_tool(tool_name, arguments or {})
    return result
