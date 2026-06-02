"""Per-FastMCP-instance registry of ``@dual_mode_tool``-decorated functions.

Keyed by FastMCP instance (via :class:`weakref.WeakKeyDictionary`) so that
multiple FastMCP servers in the same process get independent registries and
garbage collection of a server tears its registry entries down with it. This
avoids the global-mutable-state trap where decorators in one server leak into
the CLI of another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from mcp_common.dual_mode._metadata import _ToolMetadata

if TYPE_CHECKING:
    from fastmcp import FastMCP

_REGISTRY: WeakKeyDictionary[FastMCP, list[_ToolMetadata]] = WeakKeyDictionary()


def register(mcp: FastMCP, meta: _ToolMetadata) -> None:
    """Record metadata for a ``@dual_mode_tool`` invocation on ``mcp``.

    Appends ``meta`` to the per-instance list; ordering is preserved so
    ``build_cli_from_mcp`` emits commands in decoration order, matching
    Python's import-time tool registration semantics.
    """
    _REGISTRY.setdefault(mcp, []).append(meta)


def get_tools(mcp: FastMCP) -> list[_ToolMetadata]:
    """Return the metadata list for ``mcp`` (empty when nothing is registered).

    Returns a shallow copy so callers can iterate / filter without
    mutating the registry.
    """
    return list(_REGISTRY.get(mcp, []))


def tool_cli_subcommands(mcp: FastMCP) -> dict[str, list[str]]:
    """Map each registered tool name to its acceptable CLI subcommand(s).

    Returns ``{tool_name: [cli_name, *cli_aliases]}`` for every tool registered
    on ``mcp`` via :func:`mcp_common.dual_mode.dual_mode_tool`, with duplicates
    collapsed and declaration order preserved. ``mcp_only`` tools are included
    too: their CLI form is provided by other commands (declared through
    ``cli_aliases``), and an eval still needs the mapping to credit them.

    This is the bridge between the dual-mode tool definitions (source of truth
    for the canonical CLI subcommand + aliases) and the eval scorer, which is
    kept dependency-light and accepts a plain ``dict`` rather than importing
    the dual-mode framework. Typical use::

        from mcp_common.dual_mode import tool_cli_subcommands
        from mcp_common.testing.eval.scorers import cli_tool_use_scorer

        scorer = cli_tool_use_scorer(tool_subcommands=tool_cli_subcommands(mcp))

    so an expected MCP tool (e.g. ``netbox_get_objects``) is credited when the
    agent runs ANY of its declared subcommands (``list`` / ``search`` /
    ``devices``) instead of only the derived kebab-name (``get-objects``).
    """
    result: dict[str, list[str]] = {}
    for meta in _REGISTRY.get(mcp, []):
        subcommands: list[str] = []
        for sub in (meta.cli_name, *meta.cli_aliases):
            if sub not in subcommands:
                subcommands.append(sub)
        result[meta.tool_name] = subcommands
    return result


def _clear(mcp: FastMCP) -> None:
    """Drop the registry entry for ``mcp`` (test-only convenience)."""
    _REGISTRY.pop(mcp, None)
