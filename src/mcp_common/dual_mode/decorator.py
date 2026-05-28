"""``@dual_mode_tool`` — register one function as both FastMCP tool and CLI cmd.

The decorator is the user-facing entry point. It is intentionally minimal:
it records metadata in :mod:`mcp_common.dual_mode._registry` and (unless
``cli_only=True``) calls ``mcp.tool(...)`` exactly the way the user would
have done by hand. The CLI side is materialized lazily by
:func:`mcp_common.dual_mode.build_cli_from_mcp`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from mcp_common.dual_mode._metadata import _ToolMetadata
from mcp_common.dual_mode._naming import derive_cli_name
from mcp_common.dual_mode._registry import register

if TYPE_CHECKING:
    from fastmcp import FastMCP

F = TypeVar("F", bound=Callable[..., Any])

__all__ = ["dual_mode_tool"]


def dual_mode_tool(
    mcp: FastMCP,
    *,
    name: str | None = None,
    cli_name: str | None = None,
    cli_group: str | None = None,
    formatters: dict[type, Callable[[Any], str]] | None = None,
    cli_only: bool = False,
    mcp_only: bool = False,
    summary: str | None = None,
    **mcp_tool_kwargs: Any,
) -> Callable[[F], F]:
    """Register a function as both a FastMCP tool and a Typer CLI command.

    The decorator does two things at definition time:

    1. Unless ``cli_only=True``, it calls ``mcp.tool(name=..., description=...)``
       on the original function so FastMCP picks it up normally.
    2. It appends a :class:`_ToolMetadata` entry to the registry keyed by
       ``mcp`` so that a later call to
       :func:`mcp_common.dual_mode.build_cli_from_mcp` can synthesize a
       Typer command from the same function. ``mcp_only=True`` skips the
       CLI materialization step.

    The function is returned unchanged, so direct Python callers see no
    indirection. FastMCP's ``Tool`` object (which would shadow the
    callable) is registered behind the scenes; the original function
    remains importable by its module path.

    Args:
        mcp: FastMCP instance to register the tool against.
        name: FastMCP tool name. Defaults to the function's ``__name__``.
        cli_name: Typer command name. Defaults to the FastMCP tool name
            kebab-cased with the MCP namespace prefix stripped — e.g.
            tool ``netbox_lookup_device`` on ``FastMCP("netbox")`` becomes
            ``lookup-device``.
        cli_group: Optional subgroup name. When set, the CLI command is
            registered under a Typer subcommand group instead of at the
            top level (``netbox-cli devices lookup-device ...``).
        formatters: Optional ``{type: callable}`` mapping used by the CLI
            in human (non-``--json``) mode. The formatter for the return
            type — looked up by exact type, then MRO — is passed to
            :func:`mcp_common.cli.echo_result` as ``human_formatter``.
        cli_only: Skip ``mcp.tool(...)`` registration. The function is
            still added to the registry so the CLI picks it up.
        mcp_only: Skip CLI materialization. The function is registered
            with FastMCP normally and the CLI builder filters it out.
        summary: Short help text for both the FastMCP tool description
            and the Typer command short-help. Defaults to the first line
            of the docstring (with trailing punctuation preserved).
        **mcp_tool_kwargs: Extra kwargs forwarded to ``mcp.tool(...)``
            (e.g. ``annotations``, ``tags``, ``output_schema``). Ignored
            when ``cli_only=True``.

    Returns:
        The original function, unchanged.

    Raises:
        ValueError: If both ``cli_only`` and ``mcp_only`` are ``True`` —
            that combination would register the function with neither
            surface, which is almost certainly a mistake.
    """
    if cli_only and mcp_only:
        raise ValueError(
            "dual_mode_tool: cli_only=True and mcp_only=True are mutually exclusive — "
            "the function would be registered with neither FastMCP nor the CLI."
        )

    def decorator(fn: F) -> F:
        tool_name = name or fn.__name__
        resolved_cli_name = cli_name or derive_cli_name(tool_name, mcp.name)
        resolved_summary = summary if summary is not None else _first_docstring_line(fn)

        if not cli_only:
            mcp_kwargs: dict[str, Any] = {"name": tool_name}
            if resolved_summary:
                mcp_kwargs.setdefault("description", resolved_summary)
            mcp_kwargs.update(mcp_tool_kwargs)
            mcp.tool(**mcp_kwargs)(fn)

        register(
            mcp,
            _ToolMetadata(
                fn=fn,
                tool_name=tool_name,
                cli_name=resolved_cli_name,
                cli_group=cli_group,
                summary=resolved_summary,
                formatters=dict(formatters) if formatters else None,
                cli_only=cli_only,
                mcp_only=mcp_only,
                mcp_tool_kwargs=dict(mcp_tool_kwargs),
            ),
        )
        return fn

    return decorator


def _first_docstring_line(fn: Callable[..., Any]) -> str | None:
    """Return the first non-empty line of ``fn``'s docstring, or ``None``.

    Used as the default ``summary`` for both the FastMCP tool description
    and the Typer command short-help. Whitespace is stripped; trailing
    punctuation is preserved.
    """
    doc = fn.__doc__
    if not doc:
        return None
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
