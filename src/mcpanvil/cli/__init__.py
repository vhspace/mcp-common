"""CLI scaffolding helpers shared across MCP companion CLIs.

This subpackage collects the Typer/Click conventions an MCP CLI
needs so individual CLIs can focus on subcommand logic.

* :func:`create_cli_app` / :func:`run_cli` — bootstrap factory that wires
  :func:`mcpanvil.env.load_env`, :func:`mcpanvil.logging.setup_logging`,
  and :func:`mcpanvil.agent_remediation.install_cli_exception_handler`
  in the conventional order.
* :class:`SuggestingTyperGroup` — Typer group subclass that emits
  "Did you mean..." suggestions for typo'd subcommands.
* :data:`JsonOption`, :func:`echo_result`, :class:`PaginatedFormatter` —
  shared output helpers for ``--json`` / human-readable CLI commands.
* :func:`poll_until`, :class:`PollTimeout` — sync polling helper for CLI
  commands that wait on a terminal state (sync companion to
  :func:`mcpanvil.progress.poll_with_progress`).
"""

from mcpanvil.cli._bootstrap import create_cli_app, run_cli
from mcpanvil.cli._typer_group import SuggestingTyperGroup
from mcpanvil.cli.output import JsonOption, PaginatedFormatter, echo_result
from mcpanvil.cli.poll import PollTimeout, poll_until

__all__ = [
    "JsonOption",
    "PaginatedFormatter",
    "PollTimeout",
    "SuggestingTyperGroup",
    "create_cli_app",
    "echo_result",
    "poll_until",
    "run_cli",
]
