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

Enforced read-only ("eval") mode
---------------------------------
Set ``MCP_ENFORCE_READONLY`` to turn on a server-side guarantee that no
mutating tool/command executes (mcp-common#148). Read-only tools run normally;
create/update/destroy tools are refused with exactly
:data:`READONLY_REFUSAL_MESSAGE` (a terse, non-tainting one-liner) on **both**
the MCP surface (a ``ToolError`` the calling agent sees verbatim) and the CLI
(printed to stderr, non-zero exit, tool not run). Disabled by default, so an
unset variable is byte-identical to today. A tool is mutating if it is tagged
``{"write"}`` or declares ``@dual_mode_tool(..., read_only=False)``; pass
``read_only=True`` to mark a tool as never-blocked. ``MCP_ENFORCE_READONLY=1``
blocks only ``{"write"}``-tagged / ``read_only=False`` tools; the ``strict``
variant additionally blocks anything not explicitly ``read_only=True``. This is
the hard backstop that complements ``read_only_tools`` (#131), which only trims
the exposed surface harness-side. See :mod:`mcp_common.dual_mode._enforce`.

Two opt-ins close the surfaces that ``@dual_mode_tool`` does not reach on its
own:

* :func:`install_read_only_enforcement` — call once at startup on a server
  whose tools are registered **only** with plain ``@mcp.tool`` (never
  ``@dual_mode_tool``), so the MCP middleware is actually installed (otherwise
  the toggle is a silent no-op there). :func:`verify_enforcement_installed`
  logs a warning when the toggle is on but the middleware is missing.
* :func:`enforce_read_only_cli` — a decorator for **hand-written**
  ``@app.command()`` write commands (which bypass the synthesized-command
  gate), so they are refused identically on the CLI surface.
"""

from mcp_common.dual_mode._cli_enforce import (
    enforce_read_only_cli,
    refuse_if_read_only_blocked,
)
from mcp_common.dual_mode._enforce import (
    ENFORCE_READONLY_ENV_VAR,
    READONLY_REFUSAL_MESSAGE,
    EnforceMode,
    MutationClass,
    classify_mutation,
    current_enforce_mode,
    install_read_only_enforcement,
    is_blocked,
    verify_enforcement_installed,
)
from mcp_common.dual_mode._registry import tool_cli_subcommands
from mcp_common.dual_mode.builder import build_cli_from_mcp
from mcp_common.dual_mode.cli_context import CliContext
from mcp_common.dual_mode.decorator import dual_mode_tool

__all__ = [
    "ENFORCE_READONLY_ENV_VAR",
    "READONLY_REFUSAL_MESSAGE",
    "CliContext",
    "EnforceMode",
    "MutationClass",
    "build_cli_from_mcp",
    "classify_mutation",
    "current_enforce_mode",
    "dual_mode_tool",
    "enforce_read_only_cli",
    "install_read_only_enforcement",
    "is_blocked",
    "refuse_if_read_only_blocked",
    "tool_cli_subcommands",
    "verify_enforcement_installed",
]
