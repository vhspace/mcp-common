"""Shared fixtures for netbox-mcp tests."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _cli_runner_simulates_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typer's CliRunner uses non-TTY stdout; treat tests as interactive.

    Without this, auto-JSON-on-pipe would make human-mode assertions emit JSON.
    """

    def _identity(explicit_json: bool) -> bool:
        return explicit_json

    monkeypatch.setattr("mcp_common.dual_mode.builder.should_emit_json", _identity)
    monkeypatch.setattr("netbox_mcp.cli.should_emit_json", _identity)
