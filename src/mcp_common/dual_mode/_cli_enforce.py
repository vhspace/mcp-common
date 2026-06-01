"""App-level enforced read-only gate for CLI commands.

The synthesized ``build_cli_from_mcp`` commands already consult enforce mode
inside their generated body (see :func:`mcp_common.dual_mode.builder._enforce_cli_read_only`).
But a CLI may also expose **hand-written** ``@app.command()`` write commands
that never go through that synthesizer (e.g. netbox-cli's ``update-device``);
those bypass the gate. This module provides the shared, surface-agnostic CLI
gate so a hand-written command can opt in with one decorator and inherit the
**exact** same classification + refusal as everything else (DRY: it reuses
:mod:`mcp_common.dual_mode._enforce`).

Use it on any hand-written write command::

    from mcp_common.dual_mode import enforce_read_only_cli

    @app.command(name="update-device")
    @enforce_read_only_cli(read_only=False)
    def update_device(device: str, status: str | None = None, ...): ...

Under ``MCP_ENFORCE_READONLY`` (on / strict) the guard fires **before** the
command body runs — it prints exactly :data:`mcp_common.dual_mode._enforce.READONLY_REFUSAL_MESSAGE`
to **stderr** and exits non-zero, so no client is built and no write is issued.
When the toggle is unset (the default) it is a transparent pass-through.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar

import typer

from mcp_common.dual_mode._enforce import (
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
    (:func:`mcp_common.dual_mode.builder._enforce_cli_read_only`) and the
    public :func:`enforce_read_only_cli` decorator, so the classification and
    the refusal behavior never drift between them. Reads ``MCP_ENFORCE_READONLY``
    at invocation time, classifies via the shared
    :func:`mcp_common.dual_mode._enforce.classify_mutation`, and on a block
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
    """Gate a hand-written CLI command under enforced read-only mode.

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

    Args:
        read_only: Explicit classification — ``True`` (read-only, never blocked)
            or ``False`` (mutating, refused under enforce mode). ``None`` defers
            to ``tags``.
        tags: Tags consulted when ``read_only`` is ``None`` (the ``{"write"}``
            convention).

    Returns:
        A decorator that returns the wrapped function.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            refuse_if_read_only_blocked(read_only=read_only, tags=tags)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
