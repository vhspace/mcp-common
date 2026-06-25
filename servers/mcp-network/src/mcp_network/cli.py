"""network-cli — terminal access to the mcp-network switch fleet.

Thin wrapper exposing the same read-only tools as the mcp-network MCP server
via shell commands. Every command is synthesized by
:func:`mcp_common.dual_mode.build_cli_from_mcp` from the ``@dual_mode_tool``
functions in :mod:`mcp_network.server`, so the CLI and MCP surfaces share one
implementation and never drift. Output is human-readable by default; pass
``--json`` / ``-j`` for machine-parseable output.

The switch fleet inventory is loaded once when :mod:`mcp_network.server` is
imported (module-level ``site_manager.load(...)``), so no per-command client
setup is required.
"""

from __future__ import annotations

from mcp_common.cli import run_cli
from mcp_common.dual_mode import build_cli_from_mcp

from mcp_network.server import mcp, site_manager

# Build the CLI from the FastMCP server's dual-mode tools. ``build_cli_from_mcp``
# synthesizes one Typer command per ``@dual_mode_tool``: ``sites``, ``switches``,
# ``system-info``, ``port-status``, ``port-counters``, ``lldp``, ``bgp``,
# ``mac-table``, ``find-mac``, ``find-node``, ``logs``, ``wjh``. ``create_cli_app``
# (inside the builder) wires ``no_args_is_help`` + ``SuggestingTyperGroup`` +
# ``install_cli_exception_handler``, so no manual setup is needed. The synthesized
# commands route output through ``echo_result`` / ``should_emit_json``, so they
# emit JSON automatically when stdout is piped (no explicit ``--json`` needed).
# ``package_name="mcp-network"`` wires an eager root ``--version`` flag via the
# framework (mcp-common #74), replacing the hand-rolled callback that used to live
# here so the flag stays identical across every dual-mode CLI.
#
# ``before_command=site_manager.ensure_loaded`` triggers fleet loading once per
# real command (mcp-common #95). The framework skips ``before_command`` on
# introspection paths (``--version`` / ``--help`` at any level, or a bare
# invocation), so ``network-cli --version`` and ``network-cli --help`` produce
# clean stdout with no ``Loaded N site(s)`` INFO log on stderr. A real command
# (e.g. ``network-cli sites``) still logs normally on first use.
app = build_cli_from_mcp(
    mcp,
    project_repo="togethercomputer/mcp-common",
    name="network-cli",
    help="network-cli — terminal access to the mcp-network switch fleet. Use --help on any subcommand.",
    before_command=site_manager.ensure_loaded,
    package_name="mcp-network",
)


def main() -> None:
    """Entry point for ``network-cli`` console script."""
    from mcp_common.env import load_env

    load_env()
    run_cli(app, log_name="network_cli")


if __name__ == "__main__":
    main()
