"""Tests for structured output schemas, tags, and annotations on tools."""

import pytest
from fastmcp import Client

from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES
from netbox_mcp.server import VALID_DEVICE_STATUSES, mcp


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
                assert tool.annotations.readOnlyHint is False, (
                    f"Write tool {tool.name} should not be read-only"
                )
                assert tool.annotations.destructiveHint is True, (
                    f"Write tool {tool.name} should be destructive"
                )
            else:
                assert tool.annotations.readOnlyHint is True, f"Tool {tool.name} not read-only"


# ---------------------------------------------------------------------------
# Tightened input-schema invariants (netbox-mcp#126)
# ---------------------------------------------------------------------------


def _enum_of(prop: dict) -> list[str] | None:
    """Pull the ``enum`` out of a property schema, unwrapping anyOf/items."""
    if "enum" in prop:
        return prop["enum"]
    if "items" in prop:
        return _enum_of(prop["items"])
    for branch in prop.get("anyOf", []):
        found = _enum_of(branch)
        if found is not None:
            return found
    return None


@pytest.mark.anyio
async def test_object_type_params_enum_matches_constant():
    """object_type/object_types enums must equal the FULL NETBOX_OBJECT_TYPES set.

    Deriving from the constant guarantees every currently-accepted object type
    stays valid — the schema is tightened (free string -> enum) without
    narrowing accepted inputs (#126).
    """
    expected = sorted(NETBOX_OBJECT_TYPES)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for tool_name, param in [
        ("netbox_get_objects", "object_type"),
        ("netbox_get_object_by_id", "object_type"),
        ("netbox_get_objects_by_ids", "object_type"),
        ("netbox_search_objects", "object_types"),
    ]:
        prop = tools[tool_name].inputSchema["properties"][param]
        assert _enum_of(prop) == expected, f"{tool_name}.{param} enum drifted from constant"


@pytest.mark.anyio
async def test_status_params_enum_matches_constant():
    """Device-status params must expose exactly VALID_DEVICE_STATUSES as an enum."""
    expected = sorted(VALID_DEVICE_STATUSES)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for tool_name, param in [
        ("netbox_update_device", "status"),
        ("netbox_oob_summary", "status_filter"),
    ]:
        prop = tools[tool_name].inputSchema["properties"][param]
        assert sorted(_enum_of(prop)) == expected, f"{tool_name}.{param} status enum drifted"


@pytest.mark.anyio
async def test_tool_input_schemas_are_closed_objects():
    """Every tool's top-level params object forbids unknown properties.

    ``additionalProperties: false`` keeps small models from inventing params.
    The ``filters`` dict on netbox_get_objects is deliberately left open
    (additionalProperties: true) because it accepts arbitrary NetBox field
    lookups — narrowing it would reject valid queries.
    """
    async with Client(mcp) as client:
        tools = await client.list_tools()

    for tool in tools:
        assert tool.inputSchema.get("additionalProperties") is False, (
            f"{tool.name} top-level params object should be closed"
        )

    filters = {t.name: t for t in tools}["netbox_get_objects"].inputSchema["properties"]["filters"]
    assert filters.get("additionalProperties") is True, (
        "netbox_get_objects.filters must stay open for arbitrary NetBox field lookups"
    )
