"""Typer group that suggests close matches for unknown commands."""

from __future__ import annotations

import difflib
from typing import Any, ClassVar

import click
from typer.core import TyperGroup


class SuggestingTyperGroup(TyperGroup):
    """Typer group that emits ``Did you mean: ...`` on unknown commands.

    Drop-in replacement for :class:`typer.core.TyperGroup` that intercepts
    the :class:`click.UsageError` raised by :meth:`resolve_command` for
    unknown commands and prints a ``Did you mean: 'foo', 'bar'?`` line to
    stderr listing the closest matches from the registered subcommands
    (using :func:`difflib.get_close_matches`).

    When no command name is given, or no candidate clears :attr:`cutoff`,
    a plain ``click.UsageError("No such command 'X'.")`` is raised and the
    suggestion line is omitted.

    Typer's built-in ``suggest_commands`` is disabled by default in this
    subclass so the two suggestion paths do not stack. Pass
    ``suggest_commands=True`` explicitly to re-enable Typer's single-line
    "Did you mean 'X'?" rendering alongside this class's output.

    Configure suggestion matching via class attributes (subclass or use
    :meth:`with_options`):

    * :attr:`cutoff` — minimum similarity ratio passed to
      :func:`difflib.get_close_matches` (default ``0.6``).
    * :attr:`max_suggestions` — maximum number of close matches to show
      (default ``3``).

    Example::

        app = typer.Typer(cls=SuggestingTyperGroup)

        # Looser threshold, more suggestions
        app = typer.Typer(
            cls=SuggestingTyperGroup.with_options(cutoff=0.4, max_suggestions=5),
        )
    """

    cutoff: ClassVar[float] = 0.6
    max_suggestions: ClassVar[int] = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("suggest_commands", False)
        super().__init__(*args, **kwargs)

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            cmd_name = args[0] if args else None
            if not cmd_name:
                raise
            available = sorted(self.list_commands(ctx))
            matches = difflib.get_close_matches(
                cmd_name,
                available,
                n=self.max_suggestions,
                cutoff=self.cutoff,
            )
            if matches:
                suggestions = ", ".join(f"'{m}'" for m in matches)
                click.echo(f"\nDid you mean: {suggestions}?", err=True)
            raise click.UsageError(f"No such command '{cmd_name}'.") from None

    @classmethod
    def with_options(
        cls,
        *,
        cutoff: float | None = None,
        max_suggestions: int | None = None,
    ) -> type[SuggestingTyperGroup]:
        """Return a subclass with overridden ``cutoff`` / ``max_suggestions``.

        Useful when wiring :class:`SuggestingTyperGroup` into ``typer.Typer(cls=...)``
        without defining a named subclass for the override.
        """
        attrs: dict[str, Any] = {}
        if cutoff is not None:
            attrs["cutoff"] = cutoff
        if max_suggestions is not None:
            attrs["max_suggestions"] = max_suggestions
        return type(cls.__name__, (cls,), attrs)
