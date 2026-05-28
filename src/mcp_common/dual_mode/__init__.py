"""Dual-mode tool framework: single function → FastMCP tool + Typer CLI command.

The headline capability of :mod:`mcp_common`. Decorate a function with
:func:`dual_mode_tool` to register it with a FastMCP server. A later step
in this series ships :func:`build_cli_from_mcp` that materializes a Typer
CLI from the same function definitions.

For tools that take a ``fastmcp.Context`` parameter (progress reporting,
structured logging), :class:`CliContext` is the CLI-side stand-in: it
maps ``ctx.info`` / ``ctx.warning`` / ``ctx.error`` / ``ctx.debug`` /
``ctx.log`` to the standard logger and ``ctx.report_progress`` to a
``[NN%] message`` line on stderr. The builder injects it automatically
when synthesizing Typer commands for Context-using tools.
"""

from mcp_common.dual_mode.cli_context import CliContext
from mcp_common.dual_mode.decorator import dual_mode_tool

__all__ = ["CliContext", "dual_mode_tool"]
