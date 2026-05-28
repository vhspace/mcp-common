"""Dual-mode tool framework: single function → FastMCP tool + Typer CLI command.

The headline capability of :mod:`mcp_common`. Decorate a function with
:func:`dual_mode_tool` to register it with a FastMCP server. A later step
in this series ships :func:`build_cli_from_mcp` and :class:`CliContext`
that materialize a Typer CLI from the same function definitions.

The decorator side stands on its own: a function decorated with
``@dual_mode_tool`` is registered with FastMCP exactly as if you had
called ``mcp.tool(...)`` yourself, and its metadata is recorded in a
per-FastMCP-instance registry for later CLI materialization.
"""

from mcp_common.dual_mode.decorator import dual_mode_tool

__all__ = ["dual_mode_tool"]
