"""``build_cli_from_mcp`` — materialize a Typer CLI from a FastMCP instance.

Walks the per-FastMCP registry populated by ``@dual_mode_tool`` and emits a
:class:`typer.Typer` app whose commands invoke the same Python functions
the FastMCP tools call. Parameter introspection delegates to
:mod:`mcp_common.dual_mode._typer_params`; the output side routes through
:func:`mcp_common.cli.echo_result` so ``--json`` mode is uniform across
every MCP CLI.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import typer

from mcp_common.cli import JsonOption, create_cli_app, echo_result
from mcp_common.dual_mode._enforce import (
    READONLY_REFUSAL_MESSAGE,
    classify_mutation,
    current_enforce_mode,
    is_blocked,
)
from mcp_common.dual_mode._metadata import _ToolMetadata
from mcp_common.dual_mode._naming import to_kebab_case
from mcp_common.dual_mode._registry import get_tools
from mcp_common.dual_mode._typer_params import (
    _PydanticFlatten,
    iter_typer_params,
)
from mcp_common.dual_mode.cli_context import CliContext

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["build_cli_from_mcp"]


def build_cli_from_mcp(
    mcp: FastMCP,
    *,
    project_repo: str,
    name: str | None = None,
    help: str | None = None,
    before_command: Callable[[], None] | None = None,
    **typer_kwargs: Any,
) -> typer.Typer:
    """Materialize a Typer CLI from a FastMCP instance's registered dual-mode tools.

    Walks the per-``mcp`` registry populated by
    :func:`mcp_common.dual_mode.dual_mode_tool` and registers each entry
    (except ``mcp_only=True`` ones) as a Typer command. The synthesized
    command:

    * Accepts the wrapped function's parameters mapped via
      :func:`mcp_common.dual_mode._typer_params.iter_typer_params` plus a
      ``--json`` / ``-j`` flag.
    * Drives async functions with :func:`asyncio.run` and shims
      ``fastmcp.Context`` parameters with :class:`CliContext`.
    * Routes the return value through :func:`mcp_common.cli.echo_result`,
      using the per-tool ``formatters`` mapping (when provided) keyed by
      the return value's type.

    The returned app is built via :func:`mcp_common.cli.create_cli_app`,
    so the standard ``no_args_is_help`` + ``SuggestingTyperGroup`` +
    ``install_cli_exception_handler`` wiring is already attached.

    .. note::

        The :func:`install_cli_exception_handler` path (terse caller error on
        stderr; full remediation routed to the trace/diagnostic log) only runs
        when Typer is invoking the app from a real terminal — i.e. ``app()``
        from a ``__main__`` block or the entry-point script.
        :class:`typer.testing.CliRunner` bypasses Typer's outer
        exception-handling path and instead surfaces unhandled errors via
        ``result.exception`` / ``result.exit_code``, so test assertions should
        look at those attributes rather than at the rendered stderr text.
        Production CLI invocations are unaffected.

    Args:
        mcp: FastMCP instance whose dual-mode tools should be exposed.
        project_repo: GitHub repo (``owner/name``) for the agent
            remediation footer; forwarded to ``create_cli_app``.
        name: CLI app name. Defaults to ``f"{mcp.name}-cli"`` with
            non-alphanumeric runs collapsed to single dashes.
        help: Top-level help text shown by ``--help``. Defaults to a
            generic one-liner mentioning the FastMCP server name.
        before_command: Optional zero-argument callable invoked once per CLI
            invocation, AFTER Typer parses the args but BEFORE the synthesized
            tool function runs. Use it for CLI-time setup every command needs —
            instantiating the REST client, validating required env vars, etc.
            (formalizes the hand-rolled per-CLI init pattern). It is **not**
            called on introspection-only paths (``--help`` at any level, or a
            bare invocation with no subcommand), so ``<cli> --help`` and
            ``<cli> <cmd> --help`` work without credentials. The hook runs
            inside the synthesized command body, so anything it raises flows
            through the same :func:`install_cli_exception_handler` path as a
            tool error (terse caller error on stderr, full remediation to the
            trace log, non-zero exit). When ``None`` (the default) behavior is
            unchanged.
        **typer_kwargs: Extra kwargs forwarded to
            :func:`mcp_common.cli.create_cli_app`.

    Returns:
        Typer app ready to invoke or pass to
        :func:`mcp_common.cli.run_cli`.
    """
    cli_name = name or _default_cli_name(mcp.name)
    cli_help = help or f"Companion CLI for {mcp.name}."
    app = create_cli_app(cli_name, project_repo=project_repo, help=cli_help, **typer_kwargs)

    groups: dict[str, typer.Typer] = {}

    for meta in get_tools(mcp):
        if meta.mcp_only:
            continue
        _register_command(app, groups, meta, before_command=before_command)

    return app


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _default_cli_name(mcp_name: str) -> str:
    """Convert a FastMCP server name into a CLI app name.

    ``netbox-mcp`` → ``netbox-cli``, ``netbox`` → ``netbox-cli``.
    """
    kebab = to_kebab_case(mcp_name)
    if kebab.endswith("-mcp"):
        kebab = kebab[: -len("-mcp")]
    if not kebab:
        kebab = "mcp"
    return f"{kebab}-cli"


def _register_command(
    app: typer.Typer,
    groups: dict[str, typer.Typer],
    meta: _ToolMetadata,
    *,
    before_command: Callable[[], None] | None = None,
) -> None:
    """Synthesize a Typer command for ``meta`` and attach it to ``app``."""
    target_app = _resolve_group(app, groups, meta.cli_group)
    command_fn, doc = _build_command_function(meta, before_command=before_command)
    target_app.command(
        name=meta.cli_name,
        help=doc,
        short_help=meta.summary,
    )(command_fn)


def _resolve_group(
    app: typer.Typer,
    groups: dict[str, typer.Typer],
    group_name: str | None,
) -> typer.Typer:
    """Return the Typer app or subgroup commands should attach to."""
    if not group_name:
        return app
    if group_name not in groups:
        sub = typer.Typer(no_args_is_help=True, help=f"{group_name} commands.")
        groups[group_name] = sub
        app.add_typer(sub, name=group_name)
    return groups[group_name]


def _build_command_function(
    meta: _ToolMetadata,
    *,
    before_command: Callable[[], None] | None = None,
) -> tuple[Callable[..., Any], str | None]:
    """Build a sync Typer callable that invokes ``meta.fn`` end-to-end.

    Constructs a new function whose ``__signature__`` is the Typer-mapped
    parameter list (built by :func:`iter_typer_params`) plus the standard
    ``json: JsonOption = False`` flag. The body unpacks the Typer kwargs
    back to the wrapped function's expected call shape — flattened Pydantic
    fields are re-bundled into model instances, ``Context`` params get a
    :class:`CliContext` shim, and async tools are driven by ``asyncio.run``.

    When ``before_command`` is supplied it runs first inside the command
    body — i.e. only when Typer actually invokes a command (never on a
    ``--help`` / no-subcommand introspection path, where Click short-circuits
    before the callback body executes) and after Typer has parsed the args.
    Any exception it raises propagates exactly like a tool error, flowing
    through the ``install_cli_exception_handler`` wiring.
    """
    is_async = inspect.iscoroutinefunction(meta.fn)
    typer_params, original_params, context_params = iter_typer_params(meta.fn)

    @functools.wraps(meta.fn)
    def _impl(**typer_kwargs: Any) -> None:
        _enforce_cli_read_only(meta)
        if before_command is not None:
            before_command()
        as_json = bool(typer_kwargs.pop("json", False))
        call_kwargs = _rehydrate_call_kwargs(
            original_params=original_params,
            context_params=context_params,
            typer_kwargs=typer_kwargs,
        )
        result = _invoke_tool(meta.fn, call_kwargs, is_async=is_async)
        # ``echo_result`` ignores its ``truncate`` arg in JSON mode, so the
        # synthesized ``--json`` output is always complete and ``json.loads``-able
        # regardless of size (the default human-mode 4096 cap does not apply here).
        # No explicit truncate override is needed on the JSON path (#113).
        echo_result(
            result,
            as_json=as_json,
            human_formatter=_pick_formatter(meta.formatters, result),
        )

    _impl.__signature__ = _build_command_signature(typer_params)  # type: ignore[attr-defined]
    _impl.__doc__ = _build_command_doc(meta)
    _impl.__name__ = meta.cli_name.replace("-", "_")
    return _impl, _impl.__doc__


def _enforce_cli_read_only(meta: _ToolMetadata) -> None:
    """Refuse a mutating command under enforced read-only mode (CLI surface).

    Mirrors the MCP-side :class:`mcp_common.dual_mode._enforce.ReadOnlyEnforcementMiddleware`
    so both interfaces behave identically: read the ``MCP_ENFORCE_READONLY``
    toggle at invocation time, classify the command from the same
    ``read_only`` flag + ``{"write"}`` tag, and on a block print the terse,
    non-tainting refusal to **stderr** and exit non-zero **without** running
    the tool. A no-op when the toggle is unset (the default).
    """
    mode = current_enforce_mode()
    mutation = classify_mutation(meta.read_only, meta.mcp_tool_kwargs.get("tags"))
    if is_blocked(mode, mutation):
        typer.echo(READONLY_REFUSAL_MESSAGE, err=True)
        raise typer.Exit(code=1)


def _build_command_signature(typer_params: list[inspect.Parameter]) -> inspect.Signature:
    """Append the shared ``--json`` flag to the Typer-mapped parameter list.

    Uses :data:`mcp_common.cli.JsonOption` so every dual-mode CLI command
    advertises the same ``--json`` / ``-j`` semantics.
    """
    params: list[inspect.Parameter] = list(typer_params)
    params.append(
        inspect.Parameter(
            name="json",
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=False,
            annotation=JsonOption,
        )
    )
    return inspect.Signature(parameters=params)


def _build_command_doc(meta: _ToolMetadata) -> str | None:
    """Pick the help string Typer should show for the synthesized command."""
    doc = meta.fn.__doc__
    if doc:
        return inspect.cleandoc(doc)
    return meta.summary


def _rehydrate_call_kwargs(
    *,
    original_params: list[inspect.Parameter],
    context_params: list[str],
    typer_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Convert Typer-mapped kwargs back to the wrapped function's call shape.

    * Flattened Pydantic models are re-bundled from their per-field kwargs.
    * ``--params`` JSON blobs (used for Pydantic models with > the flatten
      threshold) are parsed into a model instance.
    * Context parameters receive a fresh :class:`CliContext` instance.
    """
    result: dict[str, Any] = {}
    for param in original_params:
        if param.name in context_params:
            result[param.name] = CliContext()
            continue

        flatten_info = _PydanticFlatten.from_parameter(param)
        if flatten_info is not None:
            result[param.name] = flatten_info.build_from_typer_kwargs(typer_kwargs)
            continue

        if param.name in typer_kwargs:
            result[param.name] = typer_kwargs[param.name]
    return result


def _invoke_tool(fn: Callable[..., Any], call_kwargs: dict[str, Any], *, is_async: bool) -> Any:
    """Call ``fn`` with ``call_kwargs``; drive async functions via ``asyncio.run``.

    Sync functions that mistakenly return a coroutine or async generator
    (e.g. ``return inner()`` instead of ``return await inner()``) leak
    the unawaited object through to :func:`echo_result`, which would
    ``str()`` it into ``"<coroutine object ...>"``. Catch the case here
    so the user gets a clear error pointing at the actual mistake.
    """
    if is_async:
        return asyncio.run(fn(**call_kwargs))
    result = fn(**call_kwargs)
    if inspect.iscoroutine(result) or inspect.isasyncgen(result):
        kind = type(result).__name__
        # Close the coroutine so we don't leak a "coroutine was never awaited"
        # warning on top of the actual error.
        close = getattr(result, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise RuntimeError(
            f"Tool {fn.__name__!r} is decorated as sync but returned a {kind}; "
            "declare ``async def`` (and let the framework drive ``asyncio.run``) "
            "or ``await`` the inner call yourself."
        )
    return result


def _pick_formatter(
    formatters: dict[type, Callable[[Any], str]] | None,
    result: Any,
) -> Callable[[Any], str] | None:
    """Look up the best formatter for ``result`` by exact type, then MRO."""
    if not formatters:
        return None
    result_type = type(result)
    if result_type in formatters:
        return formatters[result_type]
    for cls in result_type.__mro__:
        if cls in formatters:
            return formatters[cls]
    return None
