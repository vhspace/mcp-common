"""Trim the MCP tool surface exposed to a model under eval.

Reducing the exposed tool set is a top small-model reliability lever
(vhspace/netbox-mcp#121): a read-only eval that still exposes a **write** verb
and eval-irrelevant noise tools pushes small/fast models toward mis-selection.
netbox-mcp's read-only eval, for instance, exposed its full 8-tool surface —
including ``netbox_update_device`` (tagged ``{"write"}``) and
``netbox_get_changelogs`` — to the model under test (vhspace/netbox-mcp#122).

:func:`read_only_tools` wraps Inspect AI's
:func:`~inspect_ai.tool.mcp_tools` so each MCP eval trims its surface in **one
line** instead of hand-maintaining tool lists::

    from mcp_common.testing.eval import read_only_tools

    # netbox-mcp#122: drop the write verb + the noise tool by name
    tools = read_only_tools(server, deny=["netbox_update_device", "netbox_get_changelogs"])

    # or keep only the read tools explicitly
    tools = read_only_tools(server, allow=["netbox_get_*", "netbox_list_*"])

The returned :class:`~inspect_ai.tool.ToolSource` is a drop-in for the value
``mcp_tools`` returns — pass it wherever tools are accepted (``use_tools``,
``Task(tools=...)``, a ``react`` solver).

## Filtering dimensions

``allow`` / ``deny`` match tool **names** and accept ``fnmatch`` globs
(``"netbox_get_*"``). ``deny_tags`` matches tool **tags** (see the caveat
below). Filters compose: ``allow`` narrows first, then ``deny`` and
``deny_tags`` remove from what remains.

## Tag-filtering caveat (Inspect strips MCP tags)

Inspect AI's MCP integration resolves each MCP tool to an inspect ``Tool``
carrying only ``name`` / ``description`` / ``parameters`` — its
``_tool_def_from_mcp_tool`` drops the MCP ``Tool.meta`` / ``annotations`` where
tags live (FastMCP exposes tags at ``tool.meta["fastmcp"]["tags"]``). Inspect's
tool layer has no tag concept at all, and ``mcp_tools(server, tools=[...])``
filters by **name / glob only**. So ``deny_tags`` cannot read tags off a stock
inspect ``MCPServer`` pre- or post-connection.

To honor ``deny_tags``, supply ``tool_tags`` — a mapping of tool name → its
tags (the server's tag taxonomy, declared once). ``deny_tags`` then drops every
tool whose tags intersect it, so adding a new write-tagged tool to the server
is excluded automatically without editing the eval. Without ``tool_tags``,
prefer the name-based ``deny`` / ``allow`` forms, which inspect fully supports
(this is what mirrors netbox-mcp#122 today). If a future inspect release
surfaces MCP tags on resolved tools, those are honored too.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from inspect_ai.tool import MCPServer, Tool, ToolSource


def _tool_name(tool: Tool) -> str:
    """Resolve a (possibly MCP-sourced) inspect tool's registered name."""
    from inspect_ai.tool._tool_def import ToolDef

    return ToolDef(tool).name


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def _filter_tools(
    tools: list[Tool],
    *,
    allow: Sequence[str] | None,
    deny: Sequence[str] | None,
    deny_tags: Collection[str] | None,
    tool_tags: Mapping[str, Collection[str]] | None,
) -> list[Tool]:
    """Apply allow / deny / deny_tags filtering to a resolved tool list.

    Pure and synchronous so it is unit-testable without a live MCP server.
    ``allow`` narrows first (keep only matching names); ``deny`` then drops
    matching names; ``deny_tags`` finally drops tools whose ``tool_tags`` entry
    intersects the denied tag set. A tool with no known tags is never dropped by
    ``deny_tags``.
    """
    deny_tag_set = set(deny_tags) if deny_tags else set()

    kept: list[Tool] = []
    for tool in tools:
        name = _tool_name(tool)
        if allow is not None and not _matches_any(name, allow):
            continue
        if deny is not None and _matches_any(name, deny):
            continue
        if deny_tag_set:
            tags = set(tool_tags.get(name, ())) if tool_tags else set()
            if tags & deny_tag_set:
                continue
        kept.append(tool)
    return kept


class _ReadOnlyToolSource:
    """A :class:`~inspect_ai.tool.ToolSource` that trims another source's tools.

    Resolution is lazy: the underlying ``server`` (itself a ``ToolSource``) is
    only listed when inspect calls :meth:`tools`. When an ``allow`` list is
    given and ``server`` is a real inspect ``MCPServer``, the allow-list is
    pushed down into :func:`~inspect_ai.tool.mcp_tools` so the surface is
    narrowed natively before the remaining ``deny`` / ``deny_tags`` filters run.
    """

    def __init__(
        self,
        server: MCPServer | ToolSource,
        *,
        allow: Sequence[str] | None,
        deny: Sequence[str] | None,
        deny_tags: Collection[str] | None,
        tool_tags: Mapping[str, Collection[str]] | None,
    ) -> None:
        self._server = server
        self._allow = list(allow) if allow is not None else None
        self._deny = list(deny) if deny is not None else None
        self._deny_tags = set(deny_tags) if deny_tags else None
        self._tool_tags = tool_tags

    def _base_source(self) -> ToolSource:
        # Push the allow-list into inspect's native name/glob filter when we have
        # a real MCP server; otherwise resolve the source directly and let
        # _filter_tools apply the allow-list.
        if self._allow is not None:
            from inspect_ai.tool import MCPServer, mcp_tools

            if isinstance(self._server, MCPServer):
                return mcp_tools(self._server, tools=self._allow)
        return self._server

    async def tools(self) -> list[Tool]:
        resolved = await self._base_source().tools()
        return _filter_tools(
            resolved,
            allow=self._allow,
            deny=self._deny,
            deny_tags=self._deny_tags,
            tool_tags=self._tool_tags,
        )


def read_only_tools(
    server: MCPServer | ToolSource,
    allow: Sequence[str] | None = None,
    *,
    deny: Sequence[str] | None = None,
    deny_tags: Collection[str] | None = None,
    tool_tags: Mapping[str, Collection[str]] | None = None,
) -> ToolSource:
    """Return a trimmed :class:`~inspect_ai.tool.ToolSource` for an MCP eval.

    Wraps :func:`~inspect_ai.tool.mcp_tools` to expose only a read-only / curated
    slice of an MCP server's tools to the model under test, in one line instead
    of a hand-maintained list. See the module docstring for the tag-filtering
    caveat (inspect strips MCP tags, so ``deny_tags`` needs ``tool_tags``).

    Args:
        server: The MCP server (from ``mcp_server_stdio`` / ``mcp_server_http`` /
            ``mcp_server_sandbox``) or any inspect ``ToolSource`` to trim.
        allow: Tool names or ``fnmatch`` globs to keep. ``None`` keeps all.
        deny: Tool names or ``fnmatch`` globs to drop (e.g. the write verb plus
            eval-irrelevant noise tools). Applied after ``allow``.
        deny_tags: Tags to drop (e.g. ``{"write"}``). Requires ``tool_tags`` to
            resolve each tool's tags; see the module docstring.
        tool_tags: Mapping of tool name → its tags, used to evaluate
            ``deny_tags``.

    Returns:
        A ``ToolSource`` whose ``tools()`` yields the filtered tool list — a
        drop-in replacement for the result of ``mcp_tools(server, ...)``.
    """
    return _ReadOnlyToolSource(
        server,
        allow=allow,
        deny=deny,
        deny_tags=deny_tags,
        tool_tags=tool_tags,
    )
