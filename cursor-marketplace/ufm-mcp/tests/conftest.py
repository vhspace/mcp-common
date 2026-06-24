"""Shared pytest fixtures for the ufm-mcp test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _cli_runner_simulates_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat CliRunner invocations as interactive (TTY) by default.

    ``mcp_common.cli.should_emit_json`` auto-emits JSON when stdout is not a
    TTY, so piped/captured output is machine-readable without ``--json``. Under
    Typer's ``CliRunner`` stdout is *never* a TTY, so without this fixture every
    human-mode CLI assertion in the suite would receive JSON instead. Patch
    ``should_emit_json`` to honor only the explicit ``--json`` flag, restoring
    interactive defaults. Tests that specifically exercise the piped/non-TTY
    behavior re-patch ``should_emit_json`` locally (the later patch wins).
    """

    def _identity(explicit_json: bool) -> bool:
        return explicit_json

    monkeypatch.setattr("mcp_common.dual_mode.builder.should_emit_json", _identity)
    monkeypatch.setattr("ufm_mcp.cli.should_emit_json", _identity)
