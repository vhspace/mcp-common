"""Tests for the read-only tool-trim helper (read_only_tools / _filter_tools)."""

from __future__ import annotations

import pytest
from inspect_ai.tool import ToolSource
from inspect_ai.tool._tool import Tool
from inspect_ai.tool._tool_def import ToolDef

from mcp_common.testing.eval.tool_filters import (
    _filter_tools,
    _ReadOnlyToolSource,
    _tool_name,
    read_only_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> Tool:
    """Fabricate a resolved inspect Tool with the given registered name."""

    async def execute() -> str:
        return name

    return ToolDef(execute, name=name, description=name).as_tool()


class _FakeSource:
    """A bare ToolSource (not an inspect MCPServer) for resolution tests."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    async def tools(self) -> list[Tool]:
        return list(self._tools)


# The netbox-mcp#122 surface: 6 read tools + 1 write verb + 1 noise tool.
NETBOX_TOOLS = [
    "netbox_get_device",
    "netbox_get_objects",
    "netbox_list_devices",
    "netbox_search_objects",
    "netbox_get_object_by_id",
    "netbox_get_interfaces",
    "netbox_update_device",  # tagged {"write"}
    "netbox_get_changelogs",  # eval-irrelevant noise
]
NETBOX_TOOL_TAGS = {
    "netbox_update_device": {"write"},
}


def _names(tools: list[Tool]) -> list[str]:
    return [_tool_name(t) for t in tools]


# ---------------------------------------------------------------------------
# Pure filtering logic
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestFilterTools:
    def test_no_filters_keeps_all(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(tools, allow=None, deny=None, deny_tags=None, tool_tags=None)
        assert _names(kept) == NETBOX_TOOLS

    def test_allow_keeps_only_matching_names(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools,
            allow=["netbox_get_device", "netbox_list_devices"],
            deny=None,
            deny_tags=None,
            tool_tags=None,
        )
        assert _names(kept) == ["netbox_get_device", "netbox_list_devices"]

    def test_allow_supports_globs(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools, allow=["netbox_get_*"], deny=None, deny_tags=None, tool_tags=None
        )
        assert _names(kept) == [
            "netbox_get_device",
            "netbox_get_objects",
            "netbox_get_object_by_id",
            "netbox_get_interfaces",
            "netbox_get_changelogs",
        ]

    def test_deny_drops_matching_names(self) -> None:
        # netbox-mcp#122 one-liner: drop the write verb + the noise tool by name
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools,
            allow=None,
            deny=["netbox_update_device", "netbox_get_changelogs"],
            deny_tags=None,
            tool_tags=None,
        )
        assert _names(kept) == [
            "netbox_get_device",
            "netbox_get_objects",
            "netbox_list_devices",
            "netbox_search_objects",
            "netbox_get_object_by_id",
            "netbox_get_interfaces",
        ]

    def test_deny_supports_globs(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools, allow=None, deny=["*_changelogs", "*_update_*"], deny_tags=None, tool_tags=None
        )
        assert "netbox_get_changelogs" not in _names(kept)
        assert "netbox_update_device" not in _names(kept)
        assert len(kept) == 6

    def test_deny_tags_drops_tagged_tools(self) -> None:
        # deny-tags case: drop everything tagged {"write"} using the tag map
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools,
            allow=None,
            deny=None,
            deny_tags={"write"},
            tool_tags=NETBOX_TOOL_TAGS,
        )
        assert "netbox_update_device" not in _names(kept)
        assert len(kept) == len(NETBOX_TOOLS) - 1

    def test_deny_tags_noop_without_tool_tags(self) -> None:
        # inspect strips MCP tags, so deny_tags without a tag map keeps everything
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(tools, allow=None, deny=None, deny_tags={"write"}, tool_tags=None)
        assert _names(kept) == NETBOX_TOOLS

    def test_allow_then_deny_compose(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools,
            allow=["netbox_get_*"],
            deny=["netbox_get_changelogs"],
            deny_tags=None,
            tool_tags=None,
        )
        assert _names(kept) == [
            "netbox_get_device",
            "netbox_get_objects",
            "netbox_get_object_by_id",
            "netbox_get_interfaces",
        ]

    def test_deny_tags_composes_with_allow(self) -> None:
        tools = [_make_tool(n) for n in NETBOX_TOOLS]
        kept = _filter_tools(
            tools,
            allow=["netbox_*"],
            deny=None,
            deny_tags={"write"},
            tool_tags=NETBOX_TOOL_TAGS,
        )
        assert "netbox_update_device" not in _names(kept)


# ---------------------------------------------------------------------------
# read_only_tools (ToolSource wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestReadOnlyTools:
    def test_returns_tool_source(self) -> None:
        source = read_only_tools(_FakeSource([]), allow=["x"])
        assert isinstance(source, ToolSource)

    @pytest.mark.anyio
    async def test_allow_list_filters_resolved_tools(self) -> None:
        fake = _FakeSource([_make_tool(n) for n in NETBOX_TOOLS])
        source = read_only_tools(fake, allow=["netbox_get_device", "netbox_list_devices"])
        assert _names(await source.tools()) == ["netbox_get_device", "netbox_list_devices"]

    @pytest.mark.anyio
    async def test_deny_filters_resolved_tools(self) -> None:
        fake = _FakeSource([_make_tool(n) for n in NETBOX_TOOLS])
        source = read_only_tools(fake, deny=["netbox_update_device", "netbox_get_changelogs"])
        names = _names(await source.tools())
        assert "netbox_update_device" not in names
        assert "netbox_get_changelogs" not in names
        assert len(names) == 6

    @pytest.mark.anyio
    async def test_deny_tags_with_tool_tags(self) -> None:
        fake = _FakeSource([_make_tool(n) for n in NETBOX_TOOLS])
        source = read_only_tools(fake, deny_tags={"write"}, tool_tags=NETBOX_TOOL_TAGS)
        assert "netbox_update_device" not in _names(await source.tools())

    @pytest.mark.anyio
    async def test_deny_tags_without_tool_tags_keeps_all(self) -> None:
        fake = _FakeSource([_make_tool(n) for n in NETBOX_TOOLS])
        source = read_only_tools(fake, deny_tags={"write"})
        assert _names(await source.tools()) == NETBOX_TOOLS

    @pytest.mark.anyio
    async def test_no_filters_resolves_everything(self) -> None:
        fake = _FakeSource([_make_tool(n) for n in NETBOX_TOOLS])
        source = read_only_tools(fake)
        assert _names(await source.tools()) == NETBOX_TOOLS

    def test_non_mcp_source_resolves_directly(self) -> None:
        # a bare ToolSource is not an MCPServer, so the allow-list is applied by
        # _filter_tools rather than pushed into mcp_tools
        fake = _FakeSource([])
        wrapper = read_only_tools(fake, allow=["x"])
        assert isinstance(wrapper, _ReadOnlyToolSource)
        assert wrapper._base_source() is fake

    def test_allow_list_pushed_into_mcp_tools_for_real_server(self) -> None:
        # with a real inspect MCPServer, the allow-list is pushed down into
        # inspect's native name/glob filter (mcp_tools)
        from inspect_ai.tool import mcp_server_stdio
        from inspect_ai.tool._mcp.tools import MCPToolSourceLocal

        server = mcp_server_stdio(command="true", args=[])
        wrapper = read_only_tools(server, allow=["netbox_get_*"])
        assert isinstance(wrapper, _ReadOnlyToolSource)
        base = wrapper._base_source()
        assert isinstance(base, MCPToolSourceLocal)
        assert base._tools == ["netbox_get_*"]
