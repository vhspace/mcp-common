"""``@dual_mode_tool`` — register one function as both FastMCP tool and CLI cmd.

The decorator is the user-facing entry point. It is intentionally minimal:
it records metadata in :mod:`mcp_common.dual_mode._registry` and (unless
``cli_only=True``) calls ``mcp.tool(...)`` exactly the way the user would
have done by hand. The CLI side is materialized lazily by
:func:`mcp_common.dual_mode.build_cli_from_mcp`.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from mcp_common.dual_mode._metadata import _ToolMetadata
from mcp_common.dual_mode._naming import derive_cli_name
from mcp_common.dual_mode._registry import get_tools, register
from mcp_common.dual_mode._typer_params import (
    _resolve_hints,
    validate_supported_annotation,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

F = TypeVar("F", bound=Callable[..., Any])

__all__ = ["dual_mode_tool"]


_RESERVED_PARAM_NAMES = frozenset({"json"})
"""Parameter names the synthesized Typer command reserves for itself.

The builder appends a ``json: JsonOption = False`` keyword to every
synthesized command signature. A wrapped function with a parameter of
the same name would collide with that synthetic parameter
(``ValueError: duplicate parameter name``), so the decorator rejects
the collision up front with a clearer message.
"""

_CLI_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
"""Allowed shape for a Typer command name: lowercase kebab-case.

Validates ``cli_name`` at decoration time so a typo (e.g. whitespace
or leading dash) doesn't silently produce an unreachable command.
"""


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
        _validate_cli_name(resolved_cli_name, fn_name=fn.__name__, explicit=cli_name is not None)
        if not mcp_only:
            _check_cli_name_collision(mcp, resolved_cli_name, fn_name=fn.__name__)
        _validate_function_parameters(fn)
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


def _validate_function_parameters(fn: Callable[..., Any]) -> None:
    """Reject parameter names / annotations the framework can't surface.

    Runs once per ``@dual_mode_tool`` decoration so unsupported shapes
    fail with an actionable message at definition time rather than at
    CLI build / first-invocation time. Combines two checks:

    * Reserved parameter names (currently just ``json``) collide with
      the synthetic ``--json`` flag the builder appends to every
      command, and would otherwise surface as a confusing
      ``ValueError: duplicate parameter name`` from
      :func:`inspect.Signature` deep inside the builder.
    * Annotations Typer cannot render (``set[T]``, non-``Optional``
      unions) — :func:`validate_supported_annotation` raises with the
      offending parameter name in the message.
    """
    sig = inspect.signature(fn)
    hints = _resolve_hints(fn)
    for param in sig.parameters.values():
        if param.name in _RESERVED_PARAM_NAMES:
            raise ValueError(
                f"dual_mode_tool: parameter {param.name!r} on {fn.__name__!r} "
                f"collides with the synthetic CLI ``--{param.name}`` flag the "
                "builder injects on every command. Rename the parameter."
            )
        annotation = hints.get(param.name, param.annotation)
        validate_supported_annotation(annotation, param_name=param.name, fn_name=fn.__name__)


def _validate_cli_name(cli_name: str, *, fn_name: str, explicit: bool) -> None:
    """Reject ``cli_name`` values that produce an unreachable command.

    Typer / Click accepts almost any string but treats whitespace,
    leading dashes, and uppercase as command-line-toxic — the command
    registers but the user can never invoke it. The framework constrains
    ``cli_name`` to lowercase kebab-case (``[a-z0-9][a-z0-9-]*``) so
    typos surface at decoration time. ``explicit`` distinguishes a
    user-supplied bad ``cli_name`` from a default-derived bad one for a
    sharper error message.
    """
    if not _CLI_NAME_PATTERN.fullmatch(cli_name):
        source = "explicit cli_name" if explicit else f"default cli_name derived from {fn_name!r}"
        raise ValueError(
            f"dual_mode_tool: {source} {cli_name!r} is invalid — must match "
            f"{_CLI_NAME_PATTERN.pattern!r} (lowercase letters, digits, and "
            f"internal dashes only). Pass an explicit ``cli_name=...``."
        )


def _check_cli_name_collision(mcp: FastMCP, cli_name: str, *, fn_name: str) -> None:
    """Raise if ``cli_name`` is already registered on this FastMCP instance.

    The Typer command registry is a dict keyed by name; without this
    check, decorating a second tool with the same ``cli_name`` silently
    overwrites the first (last writer wins) — symptomatic only at CLI
    runtime when the wrong tool is invoked.
    """
    for meta in get_tools(mcp):
        if meta.cli_name == cli_name and not meta.mcp_only:
            raise ValueError(
                f"dual_mode_tool: cli_name {cli_name!r} (for {fn_name!r}) is already "
                f"registered on FastMCP({mcp.name!r}) by tool {meta.tool_name!r}. "
                "Pass an explicit ``cli_name=...`` to disambiguate."
            )


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
