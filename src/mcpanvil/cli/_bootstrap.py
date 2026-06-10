"""Typer bootstrap factory shared across MCP CLIs.

Replaces ~15 LOC of identical bootstrap that previously lived in each MCP's
``cli.py``: building a :class:`typer.Typer` with ``no_args_is_help=True``,
attaching :func:`install_cli_exception_handler` for the agent remediation
footer, and chaining ``load_env()`` + ``setup_logging()`` before invoking
the app.
"""

from __future__ import annotations

import logging
from typing import Any

import typer

from mcpanvil.agent_remediation import install_cli_exception_handler
from mcpanvil.cli._typer_group import SuggestingTyperGroup
from mcpanvil.env import load_env
from mcpanvil.logging import setup_logging

logger = logging.getLogger(__name__)


def create_cli_app(
    name: str,
    *,
    project_repo: str,
    help: str | None = None,
    **typer_kwargs: Any,
) -> typer.Typer:
    """Create a Typer app with standard MCP CLI conventions.

    Conventions applied:

    * ``no_args_is_help=True`` so running with no args prints help instead
      of silently returning success.
    * :class:`SuggestingTyperGroup` is the default ``cls`` so typo'd
      subcommands get ``Did you mean: ...`` suggestions. Override by
      passing ``cls=YourGroup``.
    * :func:`mcpanvil.agent_remediation.install_cli_exception_handler`
      is attached (with a trace logger) so unhandled exceptions print a
      terse, caller-safe error to stderr while the full remediation block
      (scoped to ``project_repo``) is routed to the trace/diagnostic log.

    Args:
        name: CLI name (used as the Typer ``name``).
        project_repo: GitHub repo ``owner/name`` for the agent remediation
            footer. Required so failure messages always include where to
            file or search issues.
        help: Top-level help text shown by ``--help``.
        **typer_kwargs: Extra kwargs forwarded to :class:`typer.Typer`.
            Pass ``cls=MyGroup`` to override the default group class.

    Returns:
        Configured :class:`typer.Typer` instance.
    """
    typer_kwargs.setdefault("no_args_is_help", True)
    typer_kwargs.setdefault("cls", SuggestingTyperGroup)
    app = typer.Typer(name=name, help=help, **typer_kwargs)
    install_cli_exception_handler(app, project_repo=project_repo, logger=logger)
    return app


def run_cli(
    app: typer.Typer,
    *,
    log_name: str,
    log_level: str | None = None,
) -> None:
    """Execute a Typer app with standard env + logging bootstrap.

    Chains, in order:

    1. :func:`mcpanvil.env.load_env` — loads ``.env`` files using
       mcpanvil's standard precedence (existing env > repo .env >
       parent .env).
    2. :func:`mcpanvil.logging.setup_logging` — configures structured
       logging on the ``log_name`` channel.
    3. ``app()`` — invoke the Typer app.

    Args:
        app: Typer app to invoke (typically built via
            :func:`create_cli_app`).
        log_name: Logger and syslog identifier (e.g. ``"netbox_cli"``).
        log_level: Log level passed to :func:`setup_logging`. When
            ``None`` (the default), ``"INFO"`` is used — matching
            :func:`setup_logging`'s own default.
    """
    load_env()
    setup_logging(name=log_name, level=log_level or "INFO")
    app()
