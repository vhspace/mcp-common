"""Tests for the read-only tool-trim helper (read_only_tools / _filter_tools)."""

from __future__ import annotations

import pytest
from inspect_ai.tool import ToolSource
from inspect_ai.tool._tool import Tool
from inspect_ai.tool._tool_def import ToolDef

from mcp_common.testing.eval.tool_filters import (
    WRITE_TAG,
    ReadOnlySurface,
    ToolSafetyInfo,
    _filter_tools,
    _ReadOnlyToolSource,
    _tool_name,
    derive_read_only_surface,
    read_only_surface_from_dual_mode,
    read_only_tools,
    read_only_tools_from_dual_mode,
    tool_safety_info_from_dual_mode,
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


# ---------------------------------------------------------------------------
# Read-only surface derivation from dual-mode metadata (#156)
# ---------------------------------------------------------------------------


def _build_widget_mcp() -> object:
    """A FastMCP server exercising every classification path of derive_read_only_surface.

    Uses the real ``@dual_mode_tool`` decorator so the derivation is tested
    against actual registry metadata (read_only flag, ``{"write"}`` tag, and the
    ``readOnlyHint`` annotation convention ``awx-mcp`` uses).
    """
    from fastmcp import FastMCP

    from mcp_common.dual_mode import dual_mode_tool

    mcp = FastMCP("widget")

    @dual_mode_tool(mcp, read_only=True)
    def widget_get_thing(name: str) -> str:
        """Get a thing."""
        return name

    @dual_mode_tool(mcp, annotations={"readOnlyHint": True})
    def widget_list_things(prefix: str = "") -> str:
        """List things."""
        return prefix

    @dual_mode_tool(mcp, read_only=False)
    def widget_delete_thing(name: str) -> str:
        """Delete a thing."""
        return name

    @dual_mode_tool(mcp, tags={"write"})
    def widget_update_thing(name: str) -> str:
        """Update a thing."""
        return name

    @dual_mode_tool(mcp)
    def widget_ambiguous(name: str) -> str:
        """Ambiguous (no classification signal)."""
        return name

    return mcp


_WIDGET_TOOL_NAMES = [
    "widget_get_thing",
    "widget_list_things",
    "widget_delete_thing",
    "widget_update_thing",
    "widget_ambiguous",
]


@pytest.mark.eval
class TestDeriveReadOnlySurface:
    def test_explicit_read_only_flag(self) -> None:
        surface = derive_read_only_surface([ToolSafetyInfo("t", read_only=True)])
        assert surface.read_only == ("t",)
        assert WRITE_TAG not in surface.tool_tags["t"]

    def test_explicit_mutating_flag_is_tagged_write(self) -> None:
        surface = derive_read_only_surface([ToolSafetyInfo("t", read_only=False)])
        assert surface.mutating == ("t",)
        # tool_tags carries WRITE_TAG so deny_tags={"write"} drops it server-agnostically
        assert WRITE_TAG in surface.tool_tags["t"]

    def test_read_only_hint_classifies_read_only(self) -> None:
        surface = derive_read_only_surface([ToolSafetyInfo("t", read_only_hint=True)])
        assert surface.read_only == ("t",)

    def test_write_tag_classifies_mutating(self) -> None:
        surface = derive_read_only_surface([ToolSafetyInfo("t", tags=frozenset({WRITE_TAG}))])
        assert surface.mutating == ("t",)
        assert WRITE_TAG in surface.tool_tags["t"]

    def test_no_signal_is_unclassified(self) -> None:
        surface = derive_read_only_surface([ToolSafetyInfo("t")])
        assert surface.unclassified == ("t",)
        assert WRITE_TAG not in surface.tool_tags["t"]

    def test_explicit_flag_overrides_hint(self) -> None:
        # read_only=False wins over a contradictory readOnlyHint=True
        surface = derive_read_only_surface(
            [ToolSafetyInfo("t", read_only=False, read_only_hint=True)]
        )
        assert surface.mutating == ("t",)

    def test_explicit_read_only_strips_contradictory_write_tag(self) -> None:
        surface = derive_read_only_surface(
            [ToolSafetyInfo("t", read_only=True, tags=frozenset({WRITE_TAG}))]
        )
        assert surface.read_only == ("t",)
        # coherence: a read-only-classified tool never carries WRITE_TAG in the map
        assert WRITE_TAG not in surface.tool_tags["t"]

    def test_preserves_non_write_tags(self) -> None:
        surface = derive_read_only_surface(
            [ToolSafetyInfo("t", read_only=True, tags=frozenset({"netbox"}))]
        )
        assert surface.tool_tags["t"] == frozenset({"netbox"})

    def test_returns_read_only_surface(self) -> None:
        assert isinstance(derive_read_only_surface([]), ReadOnlySurface)


@pytest.mark.eval
class TestDualModeDerivation:
    def test_classifies_dual_mode_tools(self) -> None:
        surface = read_only_surface_from_dual_mode(_build_widget_mcp())  # type: ignore[arg-type]
        assert set(surface.read_only) == {"widget_get_thing", "widget_list_things"}
        assert set(surface.mutating) == {"widget_delete_thing", "widget_update_thing"}
        assert set(surface.unclassified) == {"widget_ambiguous"}
        assert WRITE_TAG in surface.tool_tags["widget_delete_thing"]
        assert WRITE_TAG in surface.tool_tags["widget_update_thing"]
        assert WRITE_TAG not in surface.tool_tags["widget_get_thing"]

    def test_tool_safety_info_extracted_from_registry(self) -> None:
        infos = {i.name: i for i in tool_safety_info_from_dual_mode(_build_widget_mcp())}  # type: ignore[arg-type]
        assert infos["widget_get_thing"].read_only is True
        assert infos["widget_delete_thing"].read_only is False
        assert WRITE_TAG in infos["widget_update_thing"].tags
        assert infos["widget_list_things"].read_only_hint is True

    @pytest.mark.anyio
    async def test_read_only_tools_from_dual_mode_filters_writes(self) -> None:
        mcp = _build_widget_mcp()
        fake = _FakeSource([_make_tool(n) for n in _WIDGET_TOOL_NAMES])
        source = read_only_tools_from_dual_mode(fake, mcp)  # type: ignore[arg-type]
        resolved = _names(await source.tools())
        assert set(resolved) == {"widget_get_thing", "widget_list_things"}

    @pytest.mark.anyio
    async def test_allow_unclassified_keeps_ambiguous_but_drops_writes(self) -> None:
        mcp = _build_widget_mcp()
        fake = _FakeSource([_make_tool(n) for n in _WIDGET_TOOL_NAMES])
        source = read_only_tools_from_dual_mode(fake, mcp, allow_unclassified=True)  # type: ignore[arg-type]
        resolved = _names(await source.tools())
        assert set(resolved) == {"widget_get_thing", "widget_list_things", "widget_ambiguous"}
        assert "widget_delete_thing" not in resolved
        assert "widget_update_thing" not in resolved
