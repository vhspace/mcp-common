"""Shared MCP↔CLI parity test helpers for dual-mode tools.

Every MCP adopting the dual-mode framework (``@dual_mode_tool`` +
:func:`mcpanvil.dual_mode.build_cli_from_mcp`) needs the same assertion: the
FastMCP tool and the synthesized Typer CLI command produce equal structured
output for the same inputs. Before this module each repo hand-rolled a
``Client(mcp).call_tool`` helper, a ``CliRunner().invoke(app, ...)`` helper, and
an assert-equal — nearly identical across downstream repos, and diverging only
on the ``CliRunner`` config (``mix_stderr`` +
``result.output`` vs ``result.stdout``).

These helpers standardize the pattern::

    from mcpanvil.testing.dual_mode import (
        assert_parity,
        call_tool_via_cli,
        call_tool_via_mcp,
    )

    mcp_result = await call_tool_via_mcp(mcp, "lookup_device", hostname="b65c909e-41")
    cli_result = call_tool_via_cli(app, "lookup-device", ["--hostname", "b65c909e-41"])
    assert_parity(mcp_result, cli_result)

* :func:`call_tool_via_mcp` drives the in-memory FastMCP ``Client`` and returns
  the tool's structured output (unwrapping FastMCP's ``{"result": ...}`` envelope
  so it matches the CLI's raw ``--json`` payload).
* :func:`call_tool_via_cli` invokes the Typer app through a canonical
  :class:`~typer.testing.CliRunner` (separated stderr; ``--json`` auto-appended)
  and returns the parsed JSON from stdout, asserting a clean exit.
* :func:`assert_parity` compares the two structured results and raises with a
  unified diff on mismatch.

Install with ``uv add "mcpanvil[testing]"``; the helpers import :mod:`fastmcp`
and :mod:`typer`, both already core dependencies of mcpanvil.
"""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING, Any

from fastmcp import Client
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Sequence

    import typer
    from fastmcp import FastMCP

__all__ = [
    "assert_parity",
    "call_tool_via_cli",
    "call_tool_via_mcp",
    "make_cli_runner",
]


async def call_tool_via_mcp(mcp: FastMCP, tool_name: str, **arguments: Any) -> Any:
    """Call ``tool_name`` on ``mcp`` via an in-memory client; return structured output.

    Opens a :class:`fastmcp.Client` against the server (no network), invokes the
    tool with ``arguments``, and returns its structured result. FastMCP wraps a
    non-dict return in a ``{"result": ...}`` envelope; that sole-``result`` key is
    unwrapped here so the value matches the CLI's raw ``--json`` payload (a dict
    return is left as-is). Falls back to the first text content block for tools
    that expose no structured output.

    Note: a tool that genuinely returns a dict whose only key is ``"result"`` is
    indistinguishable from FastMCP's envelope and will be unwrapped — keep that
    key out of top-level tool returns if you rely on parity.
    """
    async with Client(mcp) as client:
        result = await client.call_tool(tool_name, arguments)
    return _structured_value(result)


def call_tool_via_cli(
    app: typer.Typer,
    command: str | Sequence[str],
    args: Sequence[str] | None = None,
    *,
    runner: CliRunner | None = None,
) -> Any:
    """Invoke a synthesized CLI command with ``--json`` and return the parsed output.

    ``command`` is the subcommand name (or a sequence of names for a grouped
    command, e.g. ``["devices", "lookup-device"]``); ``args`` are the option
    tokens (e.g. ``["--hostname", "sw01"]``). ``--json`` is appended automatically
    (unless already present) so the command emits machine-readable output, which
    is parsed from **stdout** and returned.

    Uses :func:`make_cli_runner` (separated stdout/stderr) unless a ``runner`` is
    supplied. Raises :class:`AssertionError` — with stdout/stderr/exception
    context — if the command exits non-zero or stdout is not valid JSON, so a
    parity test fails loudly rather than on a confusing ``json`` error.
    """
    runner = runner or make_cli_runner()
    command_parts = [command] if isinstance(command, str) else list(command)
    invocation: list[str] = [*command_parts, *(args or [])]
    if "--json" not in invocation and "-j" not in invocation:
        invocation.append("--json")

    result = runner.invoke(app, invocation)
    if result.exit_code != 0:
        raise AssertionError(
            f"CLI command {invocation!r} exited {result.exit_code} (expected 0).\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {_result_stderr(result)!r}\n"
            f"  exception: {result.exception!r}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI command {invocation!r} did not emit JSON on stdout ({exc}).\n"
            f"  stdout: {result.stdout!r}"
        ) from exc


def assert_parity(mcp_result: Any, cli_result: Any, *, msg: str | None = None) -> None:
    """Assert the MCP and CLI surfaces produced equal structured output.

    Both values are normalized through a JSON round-trip (so tuples-as-lists and
    key ordering never cause spurious mismatches) and compared. On mismatch a
    :class:`AssertionError` is raised carrying a unified diff of the two
    pretty-printed JSON documents.
    """
    mcp_norm = _normalize(mcp_result)
    cli_norm = _normalize(cli_result)
    if mcp_norm == cli_norm:
        return
    diff = _unified_diff(mcp_norm, cli_norm)
    prefix = f"{msg}\n" if msg else ""
    raise AssertionError(
        f"{prefix}MCP↔CLI parity mismatch — the two surfaces produced different "
        f"structured output:\n{diff}"
    )


def make_cli_runner() -> CliRunner:
    """Return a :class:`~typer.testing.CliRunner` with separated stdout/stderr.

    Standardizes the config the per-repo helpers diverged on: stdout carries only
    the command's ``--json`` payload (so :func:`json.loads` is safe) while stderr
    is captured separately. Click 8.1 needs ``mix_stderr=False`` for this; Click
    8.2 removed that parameter (streams are separated natively), so it is omitted
    there.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        # Click >= 8.2 removed ``mix_stderr`` (stdout/stderr separated natively).
        return CliRunner()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _structured_value(result: Any) -> Any:
    """Extract the comparable structured value from a FastMCP ``CallToolResult``."""
    payload = getattr(result, "structured_content", None)
    if isinstance(payload, dict):
        if set(payload.keys()) == {"result"}:
            return payload["result"]
        return payload
    if payload is not None:
        return payload
    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", None)
        if text is not None:
            return text
    return None


def _result_stderr(result: Any) -> str:
    """Return ``result.stderr`` defensively (some runners merge it into stdout)."""
    try:
        return str(result.stderr)
    except (ValueError, AttributeError):
        return "<stderr merged into stdout>"


def _normalize(value: Any) -> Any:
    """JSON round-trip ``value`` so both surfaces compare as plain JSON types."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _unified_diff(mcp_value: Any, cli_value: Any) -> str:
    mcp_lines = json.dumps(mcp_value, indent=2, sort_keys=True).splitlines()
    cli_lines = json.dumps(cli_value, indent=2, sort_keys=True).splitlines()
    return "\n".join(
        difflib.unified_diff(mcp_lines, cli_lines, fromfile="mcp", tofile="cli", lineterm="")
    )
