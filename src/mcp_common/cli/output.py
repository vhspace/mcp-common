"""CLI output helpers shared across togethercomputer MCP companion CLIs.

Centralizes the ``--json`` flag, human/JSON result rendering, and the
paginated ``{count, results: [...]}`` formatting that every togethercomputer MCP
CLI was reinventing locally.
"""

from __future__ import annotations

import inspect
import json as _json
from collections.abc import Callable
from typing import Annotated, Any

import typer

__all__ = [
    "JsonOption",
    "PaginatedFormatter",
    "echo_result",
]


def _json_default(obj: Any) -> Any:
    """Fallback serializer for :func:`json.dumps` used by :func:`echo_result`.

    Handles Pydantic v2 ``BaseModel`` instances via ``model_dump(mode="json")``
    so they serialize as JSON objects (not their repr-style string form), with
    a ``mode``-less fallback for ``model_dump`` implementations that predate
    Pydantic v2 keyword parity, then a Pydantic v1 ``.dict()`` fallback, and
    finally ``str(obj)`` so the call never raises on unknown types.
    """
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        sig_params = inspect.signature(dump).parameters
        return dump(mode="json") if "mode" in sig_params else dump()
    legacy_dict = getattr(obj, "dict", None)
    if callable(legacy_dict):
        return legacy_dict()
    return str(obj)


JsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        "-j",
        help="Output raw JSON instead of human-readable text.",
    ),
]
"""Reusable ``--json`` / ``-j`` boolean flag for CLI commands.

Use as a parameter annotation and provide the default in the signature::

    @app.command()
    def lookup(name: str, json: JsonOption = False) -> None:
        result = fetch(name)
        echo_result(result, as_json=json)
"""


def echo_result(
    data: Any,
    *,
    as_json: bool,
    human_formatter: Callable[[Any], str] | None = None,
    title: str | None = None,
    truncate: int = 4096,
) -> None:
    """Print a command result in JSON or human-readable form.

    JSON mode emits pretty-printed output with ``sort_keys=True`` so agents
    that pattern-match on shape get stable field ordering, and routes any
    non-JSON-native types through :func:`_json_default` so Pydantic models
    serialize as JSON objects rather than their repr-style strings. JSON
    output is **always emitted in full** — ``truncate`` is ignored in JSON
    mode so the payload always parses with ``json.loads`` (truncating it
    would clip mid-structure and corrupt it; see #113).

    Args:
        data: The result to display. Any JSON-serializable value (including
            Pydantic models) when ``as_json=True``; any value supported by
            ``human_formatter`` (or ``str()``) otherwise.
        as_json: When ``True``, print ``data`` as pretty-printed JSON with
            sorted keys, complete and never truncated. When ``False``, defer
            to ``human_formatter`` if given else fall back to ``str(data)``.
        human_formatter: Callable converting ``data`` to a display string
            for human mode. Ignored when ``as_json=True``.
        title: Optional title rendered (bold) above the body in human
            mode. Ignored in JSON mode so machine consumers get clean
            parseable output.
        truncate: Maximum length of the rendered body in **human mode only**.
            Human-mode strings longer than ``truncate`` are truncated with a
            ``"… (N more chars)"`` suffix; pass ``0`` to disable truncation.
            Ignored entirely when ``as_json=True`` (JSON output is never
            truncated, regardless of this value).
    """
    if as_json:
        body = _json.dumps(data, indent=2, sort_keys=True, default=_json_default)
    elif human_formatter is not None:
        body = human_formatter(data)
    else:
        body = str(data)

    # Truncation is a HUMAN-mode readability affordance only. JSON output must
    # always be emitted in full: clipping it mid-structure corrupts the payload
    # so ``json.loads`` fails, which broke ``--json`` for every dual-mode
    # synthesized command whose output exceeded ``truncate`` (#113).
    if not as_json and truncate and len(body) > truncate:
        dropped = len(body) - truncate
        body = body[:truncate] + f"… ({dropped} more chars)"

    if not as_json and title:
        typer.echo(typer.style(title, bold=True))

    typer.echo(body)


class PaginatedFormatter:
    """Format a ``{count, results: [...]}`` paginated REST response.

    Convention used by NetBox, AWX, MAAS, and similar REST APIs that
    return a count plus a list of items. Applies ``line_fmt`` to each
    item in ``results`` and prepends a ``"N result(s)"`` summary line
    by default.

    Compatible with :func:`echo_result`'s ``human_formatter`` parameter::

        line_fmt = lambda d: f"{d['name']:30s}  {d['status']}"
        echo_result(
            api_response,
            as_json=False,
            human_formatter=PaginatedFormatter(line_fmt),
        )
    """

    def __init__(
        self,
        line_fmt: Callable[[dict[str, Any]], str],
        *,
        show_count: bool = True,
    ) -> None:
        """Initialize the formatter.

        Args:
            line_fmt: Callable that takes one result dict and returns a
                one-line display string.
            show_count: When ``True`` (default), prepend a single-line
                ``"N result(s)"`` summary. Set ``False`` for compact
                pipelines where the count is redundant.
        """
        self.line_fmt = line_fmt
        self.show_count = show_count

    def __call__(self, data: Any) -> str:
        """Format ``data`` as a multi-line string.

        Non-dict inputs fall back to ``str(data)`` so the formatter never
        crashes on shape surprises (e.g. an empty REST response).
        """
        if not isinstance(data, dict):
            return str(data)
        raw_results = data.get("results", [])
        results: list[dict[str, Any]] = [r for r in raw_results if isinstance(r, dict)]
        count = data.get("count", len(results))
        lines: list[str] = []
        if self.show_count:
            lines.append(f"{count} result{'s' if count != 1 else ''}")
        for item in results:
            lines.append(self.line_fmt(item))
        return "\n".join(lines)
