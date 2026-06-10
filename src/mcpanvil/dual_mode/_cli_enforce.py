"""App-level enforced read-only gate for CLI commands.

The synthesized ``build_cli_from_mcp`` commands already consult enforce mode
inside their generated body (see :func:`mcpanvil.dual_mode.builder._enforce_cli_read_only`).
But a CLI may also expose **hand-written** ``@app.command()`` write commands
that never go through that synthesizer (e.g. netbox-cli's ``update-device``);
those bypass the gate. This module provides the shared, surface-agnostic CLI
gate so a hand-written command can opt in with one decorator and inherit the
**exact** same classification + refusal as everything else (DRY: it reuses
:mod:`mcpanvil.dual_mode._enforce`).

Use it on any hand-written write command::

    from mcpanvil.dual_mode import enforce_read_only_cli

    @app.command(name="update-device")
    @enforce_read_only_cli(read_only=False)
    def update_device(device: str, status: str | None = None, ...): ...

Under ``MCP_ENFORCE_READONLY`` (on / strict) the guard fires **before** the
command body runs — it prints exactly :data:`mcpanvil.dual_mode._enforce.READONLY_REFUSAL_MESSAGE`
to **stderr** and exits non-zero, so no client is built and no write is issued.
When the toggle is unset (the default) it is a transparent pass-through.

:func:`enforce_read_only_cli` is also **async-aware** and doubles as the
reference recipe for *any* decorator stacked under ``@dual_mode_tool``: such a
decorator MUST preserve the wrapped tool's coroutine-ness (define an
``async def`` wrapper for async tools) or the synthesized CLI rejects the tool
as "a sync callable that wraps an async tool" (#112). See its docstring for the
copy-paste recipe.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any, TypeVar

import typer

from mcpanvil.dual_mode._enforce import (
    READONLY_REFUSAL_MESSAGE,
    classify_mutation,
    current_enforce_mode,
    is_blocked,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

F = TypeVar("F", bound="Callable[..., Any]")

__all__ = ["enforce_read_only_cli", "refuse_if_read_only_blocked"]


def refuse_if_read_only_blocked(
    read_only: bool | None = None,
    tags: Iterable[str] | None = None,
) -> None:
    """Refuse the current CLI command if enforce mode blocks its classification.

    The single shared CLI gate, reused by both the synthesized commands
    (:func:`mcpanvil.dual_mode.builder._enforce_cli_read_only`) and the
    public :func:`enforce_read_only_cli` decorator, so the classification and
    the refusal behavior never drift between them. Reads ``MCP_ENFORCE_READONLY``
    at invocation time, classifies via the shared
    :func:`mcpanvil.dual_mode._enforce.classify_mutation`, and on a block
    prints exactly :data:`READONLY_REFUSAL_MESSAGE` to **stderr** and raises
    ``typer.Exit(code=1)`` — so the command body never runs. A no-op when the
    toggle is unset (the default).

    Args:
        read_only: Explicit classification (``True`` never blocked; ``False``
            mutating). ``None`` defers to the ``{"write"}`` tag convention.
        tags: Tags consulted when ``read_only`` is ``None`` (mutating if it
            contains ``"write"``).
    """
    if is_blocked(current_enforce_mode(), classify_mutation(read_only, tags)):
        typer.echo(READONLY_REFUSAL_MESSAGE, err=True)
        raise typer.Exit(code=1)


def enforce_read_only_cli(
    *,
    read_only: bool | None = None,
    tags: Iterable[str] | None = None,
) -> Callable[[F], F]:
    """Gate a hand-written CLI command (or any tool) under enforced read-only mode.

    Wrap a Typer ``@app.command()`` function so that, when
    ``MCP_ENFORCE_READONLY`` is on (or ``strict``) and the command is classified
    as blocked, the refusal fires **before** the body runs (no side effects, no
    writes). Apply it *below* ``@app.command(...)`` so Typer registers the
    guarded callable::

        @app.command(name="update-device")
        @enforce_read_only_cli(read_only=False)
        def update_device(device: str, ..., confirm: bool = False): ...

    The wrapper preserves the wrapped function's signature (via
    :func:`functools.wraps`), so Typer still introspects the original
    parameters/help; it merely interposes :func:`refuse_if_read_only_blocked`.
    A transparent pass-through when the toggle is unset (the default).

    **Async-aware — the decorator-on-dual-mode-tools recipe (#112).** This guard
    is the reference example of a decorator safe to stack *under*
    ``@dual_mode_tool``: it preserves the wrapped function's *async-ness*. When
    ``fn`` is a coroutine function it returns an ``async def`` wrapper that
    ``await``\\ s ``fn``; otherwise it returns a plain sync wrapper. This matters
    because :func:`inspect.iscoroutinefunction` does **not** follow
    ``functools.wraps``'\\ s ``__wrapped__`` — a naive *sync* wrapper around an
    ``async`` tool would look sync yet return an un-awaited coroutine, which
    :func:`mcpanvil.dual_mode.build_cli_from_mcp` then rejects (the tool "is
    registered as a sync callable but wraps an async tool"). FastMCP awaits the
    result regardless, so the bug is invisible on the MCP surface and only bites
    the CLI. **Any** decorator you stack under ``@dual_mode_tool`` must do the
    same: branch on ``inspect.iscoroutinefunction(fn)`` and define an
    ``async def`` wrapper (with :func:`functools.wraps`) for async tools, e.g.::

        def my_guard(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def awrapper(*a, **kw):
                    _check()  # guard logic
                    return await fn(*a, **kw)
                return awrapper

            @functools.wraps(fn)
            def wrapper(*a, **kw):
                _check()
                return fn(*a, **kw)
            return wrapper

    Args:
        read_only: Explicit classification — ``True`` (read-only, never blocked)
            or ``False`` (mutating, refused under enforce mode). ``None`` defers
            to ``tags``.
        tags: Tags consulted when ``read_only`` is ``None`` (the ``{"write"}``
            convention).

    Returns:
        A decorator that returns the guarded function, preserving the wrapped
        callable's sync/async nature.
    """

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                refuse_if_read_only_blocked(read_only=read_only, tags=tags)
                return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            refuse_if_read_only_blocked(read_only=read_only, tags=tags)
            return fn(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator
