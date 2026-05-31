"""Per-tool metadata recorded by ``@dual_mode_tool`` for later CLI materialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class _ToolMetadata:
    """Metadata describing one ``@dual_mode_tool``-decorated function.

    Stored in :mod:`mcp_common.dual_mode._registry` per-FastMCP-instance and
    consumed by :func:`mcp_common.dual_mode.build_cli_from_mcp` to synthesize
    Typer commands without re-decorating.

    Attributes:
        fn: Original wrapped function (sync or async). Called directly from
            the CLI; the FastMCP tool registration is whatever
            ``mcp.tool(...)`` produced when the decorator fired.
        tool_name: Name registered with FastMCP (``name`` kwarg).
        cli_name: Typer command name (already kebab-cased and de-namespaced
            relative to the FastMCP instance name).
        cli_aliases: Additional CLI subcommand names that are accepted as
            equivalent to ``cli_name`` when an eval scorer maps this MCP tool
            to its CLI form (see ``tool_cli_subcommands`` and
            ``cli_tool_use_scorer(tool_subcommands=...)``). Declared at the
            tool definition so the canonical-tool → real-subcommand mapping
            lives with the tool rather than being hand-maintained per eval.
            These are scoring equivalences, not extra runnable Typer commands.
        cli_group: Optional subgroup name. ``None`` means top-level command.
        summary: Short help text — first docstring line by default.
        formatters: Optional ``{type: callable}`` mapping for human-mode
            output rendering inside ``echo_result``.
        cli_only: Skip FastMCP registration; CLI-only command.
        mcp_only: Skip CLI materialization; MCP-tool-only.
    """

    fn: Callable[..., Any]
    tool_name: str
    cli_name: str
    cli_aliases: tuple[str, ...] = ()
    cli_group: str | None = None
    summary: str | None = None
    formatters: dict[type, Callable[[Any], str]] | None = None
    cli_only: bool = False
    mcp_only: bool = False
    mcp_tool_kwargs: dict[str, Any] = field(default_factory=dict)
