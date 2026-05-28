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


def _clear(mcp: FastMCP) -> None:
    """Drop the registry entry for ``mcp`` (test-only convenience)."""
    _REGISTRY.pop(mcp, None)
