"""Dual-mode tool framework: single function → FastMCP tool + Typer CLI command.

The headline capability of :mod:`mcp_common`. Decorate a function with
:func:`dual_mode_tool` to register it with a FastMCP server, then call
:func:`build_cli_from_mcp` to materialize a Typer CLI app whose commands
are synthesized from the same function definitions.

Example::

    from fastmcp import FastMCP
    from mcp_common.cli import run_cli
    from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool

    mcp = FastMCP("netbox-mcp")

    @dual_mode_tool(mcp, cli_name="lookup-device")
    def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
        '''Resolve a hostname/IP to a NetBox device.'''
        ...

    app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")

    if __name__ == "__main__":
        run_cli(app, log_name="netbox_cli")

The CLI command auto-derives ``lookup-device`` from the tool name (with
the ``netbox`` namespace prefix stripped) and routes the return value
through :func:`mcp_common.cli.echo_result` so ``--json`` / human modes
work uniformly.

For tools that take a ``fastmcp.Context`` parameter (progress reporting,
structured logging), :class:`CliContext` is injected automatically when
the same function runs from the CLI — see its docstring for the shimmed
methods.
"""

from mcp_common.dual_mode._registry import tool_cli_subcommands
from mcp_common.dual_mode.builder import build_cli_from_mcp
from mcp_common.dual_mode.cli_context import CliContext
from mcp_common.dual_mode.decorator import dual_mode_tool

__all__ = ["CliContext", "build_cli_from_mcp", "dual_mode_tool", "tool_cli_subcommands"]
