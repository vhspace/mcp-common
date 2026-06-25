"""Pins the dual-mode conversion of dc-support-mcp (issue #75).

dc-support-mcp adopts the dual-mode framework on the MCP side: every tool is
decorated with ``@dual_mode_tool(..., mcp_only=True)`` (so the read-only
enforcement middleware auto-installs and tools join the dual-mode registry),
while the hand-written ``dc-support-cli`` commands stay the source of truth for
the CLI surface — they carry bespoke exit codes + an auth-aware failure surface
(issue #87) and CLI-only flags the synthesized path cannot reproduce because the
tools RETURN ``{"error": ...}`` dicts instead of raising. The CLI is still built
via ``build_cli_from_mcp(..., package_name="dc-support-mcp")``, which adds a free
``--version`` flag.

These tests pin that contract: every tool is a dual-mode (``mcp_only``) tool, the
full tool + CLI surface is preserved, and ``--version`` reports the package
version.
"""

from __future__ import annotations

import pytest
from mcp_common.dual_mode._registry import get_tools
from typer.testing import CliRunner

import dc_support_mcp.mcp_server as srv
from dc_support_mcp import __version__
from dc_support_mcp.cli import app

runner = CliRunner()

# The full MCP tool surface (13 tools). ``create_vendor_ticket`` is the one tool
# with no CLI command — it stays MCP-only, mirroring the pre-refactor surface.
EXPECTED_TOOLS = {
    "get_vendor_ticket",
    "add_vendor_comment",
    "update_vendor_ticket_status",
    "list_vendor_tickets",
    "create_vendor_ticket",
    "create_vendor_service_request",
    "create_rtb_triage_ticket",
    "list_rtb_triage_tickets",
    "linear_attach_url",
    "silence_alert",
    "set_node_active",
    "search_vendor_kb",
    "get_vendor_kb_article",
}

# The full hand-written CLI command surface (14 commands). ``auth-status`` and
# ``vendors`` are CLI-only (vendor-portal credential ops with no MCP tool).
EXPECTED_CLI_COMMANDS = {
    "tickets",
    "get-ticket",
    "create-service-request",
    "comment",
    "update-ticket",
    "triage",
    "triage-list",
    "linear-attach-url",
    "set-active",
    "silence",
    "kb-search",
    "kb-article",
    "auth-status",
    "vendors",
}


@pytest.mark.unit
def test_every_tool_is_registered_dual_mode_and_mcp_only() -> None:
    """All tools went through ``@dual_mode_tool`` (registry) as ``mcp_only=True``.

    Registry membership is what makes the enforcement middleware auto-install and
    is the difference from the old plain ``@mcp.tool`` wiring; ``mcp_only=True``
    is what keeps the hand-written CLI commands authoritative (no synthesized
    command shadows them).
    """
    metas = {m.tool_name: m for m in get_tools(srv.mcp)}
    assert set(metas) == EXPECTED_TOOLS
    for name, meta in metas.items():
        assert meta.mcp_only is True, f"{name} should be registered mcp_only=True"


@pytest.mark.unit
def test_mcp_tool_surface_preserved() -> None:
    """The MCP server exposes exactly the expected 13 tools (no add/drop)."""
    import anyio

    tools = anyio.run(srv.mcp.list_tools)
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.unit
def test_cli_command_surface_preserved() -> None:
    """The CLI exposes exactly the expected 14 hand-written commands."""
    import typer

    click_cmd = typer.main.get_command(app)
    assert set(getattr(click_cmd, "commands", {})) == EXPECTED_CLI_COMMANDS


@pytest.mark.unit
def test_cli_has_version_flag() -> None:
    """``build_cli_from_mcp(package_name=...)`` adds a ``--version`` flag (#74)."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == __version__
    assert result.output.strip() == "1.16.1"


@pytest.mark.unit
def test_cli_help_lists_every_command() -> None:
    """``dc-support-cli --help`` still lists every preserved command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in EXPECTED_CLI_COMMANDS:
        assert command in result.output, f"--help is missing {command}"
