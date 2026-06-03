"""Typer group that suggests close matches for unknown commands."""

from __future__ import annotations

import difflib
import json
from typing import Any, ClassVar

import click
from typer.core import TyperGroup

# typer 0.26 vendored Click into ``typer._click``: ``TyperGroup`` now subclasses
# the *vendored* ``Command`` (not the installed ``click.Group``/``Command``), and
# its command resolution raises ``typer._click.exceptions.UsageError`` — a
# distinct class from the installed ``click.exceptions.UsageError`` (the two are
# ``is``-unequal). Catching only the installed class therefore silently misses
# the unknown-command error on typer >=0.26, so the custom suggestion / JSON
# behavior never runs and Typer's own one-line "Did you mean" leaks through.
#
# Catch whichever ``UsageError`` the active Typer raises so the behavior fires on
# both the vendored-Click line (>=0.26) and the pre-vendor line (<0.26, which
# subclasses the installed Click). On <0.26 the vendored import is absent and we
# fall back to the installed class alone.
_USAGE_ERRORS: tuple[type[Exception], ...] = (click.exceptions.UsageError,)
try:
    from typer._click.exceptions import UsageError as _VendoredUsageError
except ImportError:  # typer < 0.26 — no vendored Click
    pass
else:
    _USAGE_ERRORS = (_VendoredUsageError, click.exceptions.UsageError)


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

    **JSON error mode.** When ``--json`` or ``-j`` appears anywhere in the
    invocation args, the human ``Did you mean`` line is replaced by a single
    structured JSON object on stderr so agents and pipelines get a
    machine-parseable error instead of prose::

        {
          "error": "No such command 'lookpu'.",
          "suggestions": ["lookup"],
          "available_commands": ["get", "list", "lookup", "search"]
        }

    The process then exits with status ``2`` (the standard usage-error code)
    without Click also rendering its own ``Error:`` text, so stderr carries
    exactly one JSON document. This generalizes the per-MCP custom groups
    (e.g. netbox's ``_NetBoxGroup``) so downstream CLIs can delete them. The
    JSON-flag detection is a heuristic scan of the raw args; a value that
    happens to equal ``--json`` for an *unknown* command still triggers JSON
    mode (the command is unknown either way).

    Typer's built-in ``suggest_commands`` is forced off in this subclass so the
    two suggestion paths never stack: this class is the single source of
    unknown-command output. (typer >=0.26 defaults ``suggest_commands=True`` and
    forwards it to the group, so it is overwritten in ``__init__`` rather than
    merely defaulted.)

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
        # Force Typer's built-in single-line suggester off so it never stacks on
        # top of this class's output. ``setdefault`` is not enough on typer
        # >=0.26: ``Typer()`` defaults ``suggest_commands=True`` and forwards it
        # explicitly to the group, so the key is always present. Overwriting it
        # makes this class the single source of suggestion / JSON-error output.
        kwargs["suggest_commands"] = False
        super().__init__(*args, **kwargs)

    def resolve_command(self, ctx: Any, args: list[str]) -> tuple[str | None, Any, list[str]]:
        # ``ctx`` / the resolved command are annotated ``Any`` rather than
        # ``click.Context`` / ``click.Command``: typer >=0.26 vendored Click, so
        # the supertype's signature uses ``typer._click`` types (not the
        # installed ``click`` ones). ``Any`` keeps this override LSP-compatible
        # across both the vendored (>=0.26) and installed (<0.26) Click lines.
        try:
            return super().resolve_command(ctx, args)
        except _USAGE_ERRORS as exc:
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
            if _wants_json(args):
                # Structured error for agents/pipelines. Emit a single JSON
                # document on stderr (matching the shape downstream custom
                # groups used) and exit 2 directly so Typer/Click does not also
                # render its own ``Error:`` text on top of the JSON.
                error_data = {
                    "error": f"No such command '{cmd_name}'.",
                    "suggestions": matches,
                    "available_commands": available,
                }
                click.echo(json.dumps(error_data, indent=2), err=True)
                raise SystemExit(2) from None
            if matches:
                suggestions = ", ".join(f"'{m}'" for m in matches)
                click.echo(f"\nDid you mean: {suggestions}?", err=True)
            # Re-raise the SAME usage error Typer raised (vendored-Click class on
            # >=0.26, installed-Click class before) so Typer's error handler
            # renders it and exits 2. Reset the message to the canonical
            # "No such command 'X'." so none of Typer's own appended suggestion
            # text survives alongside our line above.
            exc.message = f"No such command '{cmd_name}'."  # type: ignore[attr-defined]
            raise exc from None

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


def _wants_json(args: list[str]) -> bool:
    """Return ``True`` if ``--json`` / ``-j`` appears in the raw invocation args.

    A deliberately simple membership scan, matching the JSON-detection
    heuristic the per-MCP custom groups used. Reliable for the common
    ``<cli> <unknown-cmd> --json`` / ``-j`` form; the value-vs-flag ambiguity
    only matters for an already-unknown command, so erring toward the
    machine-readable error is the safer behavior for agents.
    """
    return "--json" in args or "-j" in args
