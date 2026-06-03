"""CLI scaffolding helpers shared across togethercomputer MCP companion CLIs.

This subpackage collects the Typer/Click conventions every togethercomputer MCP CLI
needs so individual CLIs can focus on subcommand logic.

* :func:`create_cli_app` / :func:`run_cli` — bootstrap factory that wires
  :func:`mcp_common.env.load_env`, :func:`mcp_common.logging.setup_logging`,
  and :func:`mcp_common.agent_remediation.install_cli_exception_handler`
  in the conventional order.
* :class:`SuggestingTyperGroup` — Typer group subclass that emits
  "Did you mean..." suggestions for typo'd subcommands.
* :data:`JsonOption`, :func:`echo_result`, :class:`PaginatedFormatter` —
  shared output helpers for ``--json`` / human-readable CLI commands.
* :func:`poll_until`, :class:`PollTimeout` — sync polling helper for CLI
  commands that wait on a terminal state (sync companion to
  :func:`mcp_common.progress.poll_with_progress`).
"""

from mcp_common.cli._bootstrap import create_cli_app, run_cli
from mcp_common.cli._typer_group import SuggestingTyperGroup
from mcp_common.cli.output import JsonOption, PaginatedFormatter, echo_result, should_emit_json
from mcp_common.cli.poll import PollTimeout, poll_until

__all__ = [
    "JsonOption",
    "PaginatedFormatter",
    "PollTimeout",
    "SuggestingTyperGroup",
    "create_cli_app",
    "echo_result",
    "poll_until",
    "run_cli",
    "should_emit_json",
]
