"""Trim the MCP tool surface exposed to a model under eval.

Reducing the exposed tool set is a top small-model reliability lever
(togethercomputer/netbox-mcp#121): a read-only eval that still exposes a **write** verb
and eval-irrelevant noise tools pushes small/fast models toward mis-selection.
netbox-mcp's read-only eval, for instance, exposed its full 8-tool surface —
including ``netbox_update_device`` (tagged ``{"write"}``) and
``netbox_get_changelogs`` — to the model under test (togethercomputer/netbox-mcp#122).

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

## Deriving the read-only surface from dual-mode metadata (#156)

Hand-maintaining an ``allow`` / ``tool_tags`` list per write-capable server
drifts as the server gains tools. :func:`read_only_tools_from_dual_mode` instead
**derives** the surface from a FastMCP server's ``@dual_mode_tool`` metadata so
the trim stays in sync with the server with no per-eval list:

- a tool's explicit ``read_only`` flag (``@dual_mode_tool(..., read_only=True)``)
  is authoritative;
- otherwise an ``annotations={"readOnlyHint": True}`` (the convention
  ``awx-mcp`` sets on its read tools) marks it read-only;
- a ``{"write"}`` **tag** (or ``read_only=False``) marks it mutating.

:func:`derive_read_only_surface` is the pure classifier (testable without a live
server); it also emits a ``tool_tags`` map in which every mutating tool carries
the :data:`WRITE_TAG`, so ``read_only_tools(server, deny_tags={"write"},
tool_tags=...)`` drops writes **server-agnostically** — including a *future*
write tool — the moment the server adopts the ``{"write"}`` tag convention.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from fastmcp import FastMCP
    from inspect_ai.tool import MCPServer, Tool, ToolSource

WRITE_TAG = "write"
"""Tag convention marking a tool as **mutating** (create / update / destroy).

Mirrors ``mcp_common.dual_mode._enforce.WRITE_TAG`` — duplicated here (with a
parity test) so this dependency-light filter module need not import the
dual-mode framework just to name the convention. A server that tags its write
tools ``tags={"write"}`` gets ``read_only_tools(deny_tags={"write"})`` filtering
and :func:`derive_read_only_surface` classification for free.
"""


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


# ---------------------------------------------------------------------------
# Read-only surface derivation from dual-mode metadata (#156)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSafetyInfo:
    """The mutation-classification inputs for a single tool.

    A server-agnostic descriptor so :func:`derive_read_only_surface` can be
    unit-tested without a live FastMCP server. Build one per tool from whatever
    metadata is available (``@dual_mode_tool`` fields, FastMCP tags/annotations).

    Attributes:
        name: The tool's registered name.
        read_only: Explicit mutation flag (``@dual_mode_tool(read_only=...)``).
            ``True`` = read-only, ``False`` = mutating, ``None`` = defer to
            ``read_only_hint`` then the :data:`WRITE_TAG` convention.
        tags: The tool's tags (the ``{"write"}`` convention lives here).
        read_only_hint: The MCP ``annotations.readOnlyHint`` value when set
            (``awx-mcp`` marks its read tools this way); ``True`` classifies the
            tool read-only when no explicit ``read_only`` flag overrides it.
    """

    name: str
    read_only: bool | None = None
    tags: frozenset[str] = field(default_factory=frozenset)
    read_only_hint: bool | None = None


@dataclass(frozen=True)
class ReadOnlySurface:
    """The derived read-only tool surface for an MCP eval (#156).

    Attributes:
        read_only: Names classified read-only — the natural ``allow`` list for
            :func:`read_only_tools`.
        mutating: Names classified mutating (refused under enforce mode).
        unclassified: Names with no read-only/mutating signal — kept out of the
            strict allow-list but not proven to write.
        tool_tags: ``{name: tags}`` for every tool, with :data:`WRITE_TAG`
            guaranteed present on each mutating tool, so
            ``read_only_tools(server, deny_tags={"write"}, tool_tags=surface
            .tool_tags)`` drops writes even if the server has not (yet) tagged
            them ``{"write"}`` itself.
    """

    read_only: tuple[str, ...]
    mutating: tuple[str, ...]
    unclassified: tuple[str, ...]
    tool_tags: dict[str, frozenset[str]]


def derive_read_only_surface(tools: Iterable[ToolSafetyInfo]) -> ReadOnlySurface:
    """Classify ``tools`` into read-only / mutating / unclassified (pure).

    Precedence (highest first), so an explicit declaration always wins over a
    weaker hint:

    1. ``read_only is True`` → read-only; ``read_only is False`` → mutating.
    2. ``read_only_hint is True`` → read-only (the ``readOnlyHint`` annotation).
    3. :data:`WRITE_TAG` in ``tags`` → mutating.
    4. otherwise → unclassified.

    The returned :attr:`ReadOnlySurface.tool_tags` carries each tool's own tags
    and additionally guarantees :data:`WRITE_TAG` on every mutating tool, so the
    map drives ``deny_tags={"write"}`` filtering consistently regardless of how
    the tool was classified.
    """
    read_only: list[str] = []
    mutating: list[str] = []
    unclassified: list[str] = []
    tool_tags: dict[str, frozenset[str]] = {}

    for info in tools:
        tags = set(info.tags)
        if info.read_only is True:
            read_only.append(info.name)
        elif info.read_only is False:
            mutating.append(info.name)
        elif info.read_only_hint is True:
            read_only.append(info.name)
        elif WRITE_TAG in tags:
            mutating.append(info.name)
        else:
            unclassified.append(info.name)

        # Keep the WRITE_TAG presence coherent with the classification so the
        # emitted map drives ``deny_tags={"write"}`` exactly: every mutating tool
        # carries it, and a tool classified read-only by an explicit flag/hint
        # never does (even if its raw tags contradictorily included it).
        if info.name in mutating:
            tags.add(WRITE_TAG)
        else:
            tags.discard(WRITE_TAG)
        tool_tags[info.name] = frozenset(tags)

    return ReadOnlySurface(
        read_only=tuple(read_only),
        mutating=tuple(mutating),
        unclassified=tuple(unclassified),
        tool_tags=tool_tags,
    )


def _read_only_hint(annotations: Any) -> bool | None:
    """Best-effort extract ``readOnlyHint`` from a FastMCP ``annotations`` value.

    Tolerates both the plain-``dict`` form (``annotations={"readOnlyHint":
    True}``) and a ``ToolAnnotations``-style object exposing a ``readOnlyHint``
    attribute. Returns ``None`` when no hint is present.
    """
    if annotations is None:
        return None
    if isinstance(annotations, Mapping):
        value = annotations.get("readOnlyHint")
    else:
        value = getattr(annotations, "readOnlyHint", None)
    return value if isinstance(value, bool) else None


def tool_safety_info_from_dual_mode(mcp: FastMCP) -> list[ToolSafetyInfo]:
    """Build :class:`ToolSafetyInfo` for every ``@dual_mode_tool`` on ``mcp``.

    Reads the dual-mode registry (the source of truth for each tool's
    ``read_only`` flag, ``tags``, and ``annotations``) so the read-only surface
    derivation stays in lockstep with the server's tool definitions. Tools
    registered with a plain ``@mcp.tool`` (never ``@dual_mode_tool``) are not in
    the registry and so are not returned — classify those via the ``{"write"}``
    tag convention with :func:`read_only_tools` directly.
    """
    from mcp_common.dual_mode._registry import get_tools

    infos: list[ToolSafetyInfo] = []
    for meta in get_tools(mcp):
        kwargs = meta.mcp_tool_kwargs or {}
        raw_tags = kwargs.get("tags") or ()
        infos.append(
            ToolSafetyInfo(
                name=meta.tool_name,
                read_only=meta.read_only,
                tags=frozenset(raw_tags),
                read_only_hint=_read_only_hint(kwargs.get("annotations")),
            )
        )
    return infos


def read_only_surface_from_dual_mode(mcp: FastMCP) -> ReadOnlySurface:
    """Derive the :class:`ReadOnlySurface` for ``mcp`` from its dual-mode metadata."""
    return derive_read_only_surface(tool_safety_info_from_dual_mode(mcp))


def read_only_tools_from_dual_mode(
    server: MCPServer | ToolSource,
    mcp: FastMCP,
    *,
    allow_unclassified: bool = False,
    deny: Sequence[str] | None = None,
    extra_deny_tags: Collection[str] | None = None,
) -> ToolSource:
    """Return a read-only :class:`~inspect_ai.tool.ToolSource` derived from ``mcp``.

    Combines :func:`read_only_surface_from_dual_mode` with
    :func:`read_only_tools`: the allow-list is the tools classified read-only
    (plus the unclassified ones when ``allow_unclassified=True``), and
    ``deny_tags`` is ``{"write"}`` (plus ``extra_deny_tags``) backed by the derived
    ``tool_tags`` map so any mutating tool is dropped even if it slipped past the
    allow-list.

    The two arguments are **distinct objects**: ``server`` is the inspect-side
    tool source the eval actually filters (e.g. from ``mcp_server_stdio`` /
    ``mcp_server_http``), while ``mcp`` is the **server-side FastMCP instance**
    whose ``@dual_mode_tool`` registry is read to classify the surface. They
    describe the same server, so the derived tool *names* line up with the names
    inspect resolves.

    Args:
        server: The inspect MCP server / ``ToolSource`` to trim (the value you
            would otherwise pass to :func:`read_only_tools`).
        mcp: The server-side FastMCP instance whose dual-mode registry supplies
            the read-only / write classification.
        allow_unclassified: Keep tools with no read-only/mutating signal in the
            allow-list (defaults to ``False`` — strictest surface: only proven
            read-only tools).
        deny: Extra tool names / globs to drop (forwarded to
            :func:`read_only_tools`).
        extra_deny_tags: Additional tags to deny alongside :data:`WRITE_TAG`.

    Returns:
        A trimmed ``ToolSource`` exposing only the read-only surface.
    """
    surface = read_only_surface_from_dual_mode(mcp)
    allow = list(surface.read_only)
    if allow_unclassified:
        allow.extend(surface.unclassified)
    deny_tags: set[str] = {WRITE_TAG}
    if extra_deny_tags:
        deny_tags.update(extra_deny_tags)
    return read_only_tools(
        server,
        allow=allow or None,
        deny=deny,
        deny_tags=deny_tags,
        tool_tags=surface.tool_tags,
    )
