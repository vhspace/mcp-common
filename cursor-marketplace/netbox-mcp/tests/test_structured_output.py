"""Tests for structured output schemas, tags, and annotations on tools."""

import pytest
from fastmcp import Client

from netbox_mcp.server import mcp


@pytest.mark.anyio
async def test_tools_have_output_schemas():
    """Tools with predictable shapes should expose output schemas."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_map = {t.name: t for t in tools}

        lookup = tool_map["netbox_lookup_device"]
        assert lookup.outputSchema is not None
        assert lookup.outputSchema["properties"]["count"]["type"] == "integer"

        get_objs = tool_map["netbox_get_objects"]
        assert get_objs.outputSchema is not None
        assert "results" in get_objs.outputSchema["properties"]

        changelogs = tool_map["netbox_get_changelogs"]
        assert changelogs.outputSchema is not None

        search = tool_map["netbox_search_objects"]
        assert search.outputSchema is not None


@pytest.mark.anyio
async def test_tools_have_tags():
    """All tools should have at least one tag (server-side metadata)."""
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.tags, f"Tool {tool.name} has no tags"


_WRITE_TOOLS = frozenset({"netbox_update_device"})


@pytest.mark.anyio
async def test_tool_annotations_are_read_only():
    """Read-only tools should be marked read-only; write tools should not."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        for tool in tools:
            assert tool.annotations is not None, f"Tool {tool.name} has no annotations"
            if tool.name in _WRITE_TOOLS:
                assert tool.annotations.readOnlyHint is False, f"Write tool {tool.name} should not be read-only"
                assert tool.annotations.destructiveHint is True, f"Write tool {tool.name} should be destructive"
            else:
                assert tool.annotations.readOnlyHint is True, f"Tool {tool.name} not read-only"
