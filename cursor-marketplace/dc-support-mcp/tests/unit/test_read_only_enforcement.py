"""Read-only enforcement wiring for the dc-support-mcp server.

dc-support-mcp registers every tool with ``@dual_mode_tool(..., mcp_only=True)``,
so the ``MCP_ENFORCE_READONLY`` backstop middleware auto-installs via the
idempotent ``ensure_enforcement_installed`` the decorator calls — no explicit
``install_read_only_enforcement`` call is needed anymore. Mutating tools stay
tagged ``tags={"write"}``. These tests pin that contract without touching any
vendor portal or browser: the middleware refusal fires *before* the tool body
runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anyio
import pytest
from fastmcp.exceptions import ToolError
from mcp_common.dual_mode import (
    READONLY_REFUSAL_MESSAGE,
    EnforceMode,
    MutationClass,
    current_enforce_mode,
    is_blocked,
)
from mcp_common.dual_mode._enforce import (
    ReadOnlyEnforcementMiddleware,
    _classify_registered_tool,
)
from mcp_common.testing.dual_mode import make_cli_runner

import dc_support_mcp.mcp_server as srv
from dc_support_mcp.cli import app

WRITE_TOOLS = {
    "add_vendor_comment",
    "update_vendor_ticket_status",
    "create_vendor_ticket",
    "create_vendor_service_request",
    "create_rtb_triage_ticket",
    "linear_attach_url",
    "silence_alert",
    "set_node_active",
}

READ_TOOLS = {
    "get_vendor_ticket",
    "list_vendor_tickets",
    "list_rtb_triage_tickets",
    "search_vendor_kb",
    "get_vendor_kb_article",
}


def _tool_tags(name: str) -> set[str]:
    async def _go() -> set[str]:
        tool = await srv.mcp.get_tool(name)
        return set(tool.tags or ())

    return anyio.run(_go)


def test_enforcement_middleware_installed() -> None:
    assert any(isinstance(m, ReadOnlyEnforcementMiddleware) for m in srv.mcp.middleware), (
        "@dual_mode_tool must auto-install the enforcement middleware at import "
        "so the MCP_ENFORCE_READONLY toggle is not a no-op"
    )


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_carry_write_tag(name: str) -> None:
    assert "write" in _tool_tags(name)


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_read_tools_are_not_write_tagged(name: str) -> None:
    assert "write" not in _tool_tags(name)


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_classified_mutating(name: str) -> None:
    mutation = anyio.run(_classify_registered_tool, srv.mcp, name)
    assert mutation is MutationClass.MUTATING
    assert is_blocked(EnforceMode.ENABLED, mutation)


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_read_tools_run_under_enabled_mode(name: str) -> None:
    mutation = anyio.run(_classify_registered_tool, srv.mcp, name)
    assert not is_blocked(EnforceMode.ENABLED, mutation)


def test_toggle_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    assert current_enforce_mode() is EnforceMode.OFF


def test_middleware_refuses_write_before_running_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutating call is refused with the terse message and never reaches the body."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    middleware = next(m for m in srv.mcp.middleware if isinstance(m, ReadOnlyEnforcementMiddleware))

    class _Message:
        name = "create_vendor_ticket"

    class _Context:
        message = _Message()

    async def _call_next(_ctx: object) -> object:
        raise AssertionError("write tool body must not execute under enforced read-only mode")

    async def _go() -> object:
        return await middleware.on_call_tool(_Context(), _call_next)

    with pytest.raises(ToolError) as excinfo:
        anyio.run(_go)
    assert str(excinfo.value) == READONLY_REFUSAL_MESSAGE


def test_middleware_passthrough_when_toggle_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the toggle unset the middleware is a transparent pass-through."""
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    middleware = next(m for m in srv.mcp.middleware if isinstance(m, ReadOnlyEnforcementMiddleware))
    sentinel = object()

    class _Message:
        name = "create_vendor_ticket"

    class _Context:
        message = _Message()

    async def _call_next(_ctx: object) -> object:
        return sentinel

    async def _go() -> object:
        return await middleware.on_call_tool(_Context(), _call_next)

    assert anyio.run(_go) is sentinel


# ── CLI write-command enforcement (the gap this change closes) ──────────────
#
# The MCP surface auto-installs the ``MCP_ENFORCE_READONLY`` middleware (pinned
# above), but the hand-written ``@app.command()`` WRITE commands bypass the
# ``build_cli_from_mcp`` synthesizer, so each now carries an explicit
# ``@enforce_read_only_cli(read_only=False)`` gate — symmetric with the eight
# ``tags={"write"}`` MCP tools. ``create_vendor_ticket`` has no CLI counterpart,
# so the CLI exposes 7 of those 8 as write commands. These tests pin that the
# gate refuses each write command BEFORE its body (no handler/network/Linear/RTB
# side effect), while read commands and ``--help`` stay usable and the bespoke
# #87 auth-aware exit codes are untouched (the gate is a transparent pass-through
# when the toggle is unset).

cli_runner = make_cli_runner()

# Minimal Typer-valid invocations for each gated write command. Under enforcement
# the body never runs (the gate raises first), so only the *required*
# arguments/options matter here.
WRITE_CLI_INVOCATIONS: dict[str, list[str]] = {
    "comment": ["comment", "SUPP-1", "--text", "hi", "--vendor", "ori"],
    "update-ticket": ["update-ticket", "SUPP-1", "--status", "resolved", "--vendor", "ori"],
    "create-service-request": [
        "create-service-request",
        "--summary",
        "s",
        "--description",
        "d",
        "--vendor",
        "ori",
    ],
    "triage": ["triage", "--device", "node-1", "--summary", "boom"],
    "linear-attach-url": [
        "linear-attach-url",
        "SRE-1",
        "--url",
        "https://example.test/pr",
        "--title",
        "t",
    ],
    "set-active": ["set-active", "--device", "node-1"],
    "silence": ["silence", "--instance", "node-1.cloud.together.ai:.*"],
}

# CLI write commands that gained the gate, paired to their MCP write tool. The
# eighth MCP write tool (``create_vendor_ticket``) has no CLI command.
CLI_WRITE_TO_MCP_TOOL = {
    "comment": "add_vendor_comment",
    "update-ticket": "update_vendor_ticket_status",
    "create-service-request": "create_vendor_service_request",
    "triage": "create_rtb_triage_ticket",
    "linear-attach-url": "linear_attach_url",
    "set-active": "set_node_active",
    "silence": "silence_alert",
}


def test_gated_cli_write_commands_mirror_mcp_write_tools() -> None:
    """The 7 gated CLI write commands map onto MCP write tools (minus create_vendor_ticket)."""
    assert set(WRITE_CLI_INVOCATIONS) == set(CLI_WRITE_TO_MCP_TOOL)
    assert set(CLI_WRITE_TO_MCP_TOOL.values()) == WRITE_TOOLS - {"create_vendor_ticket"}


@pytest.mark.parametrize(
    "argv",
    [argv for _, argv in sorted(WRITE_CLI_INVOCATIONS.items())],
    ids=sorted(WRITE_CLI_INVOCATIONS),
)
def test_cli_write_command_refused_under_enforcement(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every hand-written WRITE command is refused under ``MCP_ENFORCE_READONLY=1``.

    The exact refusal on stderr can only appear if the gate fired *before* the
    body. Defensive patches guarantee that even a regressed gate cannot perform
    real handler auth or an Alertmanager/RTB/Linear write — it would raise or
    early-exit instead, failing this assertion loudly.
    """
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    monkeypatch.delenv("RTB_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("write command body must not run under enforced read-only mode")

    with (
        patch("dc_support_mcp.cli._get_handler", side_effect=_boom),
        patch("dc_support_mcp.formatting.alertmanager_create_silence", side_effect=_boom),
    ):
        result = cli_runner.invoke(app, argv)

    assert result.exit_code != 0
    assert result.stderr.strip() == READONLY_REFUSAL_MESSAGE


def test_cli_write_gate_fires_before_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """``comment`` is refused before any vendor handler is constructed."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    with patch("dc_support_mcp.cli._get_handler") as mock_get_handler:
        result = cli_runner.invoke(app, ["comment", "SUPP-1", "--text", "hi", "--vendor", "ori"])
    assert result.exit_code != 0
    assert result.stderr.strip() == READONLY_REFUSAL_MESSAGE
    mock_get_handler.assert_not_called()


def test_cli_write_runs_when_toggle_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the toggle unset the gate is a transparent pass-through — the body runs."""
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    handler = MagicMock()
    handler.add_comment.return_value = {"ok": True}
    handler.last_error = None
    with patch("dc_support_mcp.cli._get_handler", return_value=handler):
        result = cli_runner.invoke(app, ["comment", "SUPP-1", "--text", "hi", "--vendor", "ori"])
    assert result.exit_code == 0, result.stderr
    handler.add_comment.assert_called_once()


@pytest.mark.parametrize("command", sorted(WRITE_CLI_INVOCATIONS))
def test_cli_write_help_works_under_enforcement(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--help`` for a gated write command still works under enforcement.

    Click resolves ``--help`` during parsing, before the gated callable runs, so
    the gate never fires for a help request.
    """
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    result = cli_runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.stderr
    assert "Usage" in result.output


def test_cli_read_command_runs_under_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read command (``tickets``) is NOT gated — its body runs under enforcement."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    handler = MagicMock()
    handler.list_tickets.return_value = []
    handler.last_error = None
    with patch("dc_support_mcp.cli._get_handler", return_value=handler):
        result = cli_runner.invoke(app, ["tickets", "--vendor", "ori"])
    assert result.exit_code == 0, result.stderr
    assert "No tickets found" in result.output
    handler.list_tickets.assert_called_once()
