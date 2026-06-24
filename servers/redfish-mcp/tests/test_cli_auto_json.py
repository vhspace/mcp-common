"""Auto-JSON-when-piped behavior for redfish-cli (#82).

redfish-cli adopts ``mcp_common.cli.should_emit_json`` so non-TTY (piped /
captured) invocations emit machine-readable JSON by default, while an
interactive TTY keeps the human rendering and an explicit ``--json`` always
wins. The autouse conftest fixture simulates a TTY for the rest of the suite;
these tests drive each branch explicitly via the ``health`` command.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from redfish_mcp.cli import app

runner = CliRunner()

MOCK_HOST = "192.168.1.100"


def _fake_client() -> MagicMock:
    """A RedfishClient stand-in whose system payload drives ``health`` output."""
    client = MagicMock()
    endpoint = MagicMock()
    endpoint.system_url = f"https://{MOCK_HOST}/redfish/v1/Systems/1"
    client.discover_system.return_value = endpoint
    client.get_json.return_value = {
        "PowerState": "On",
        "Status": {"Health": "OK", "HealthRollup": "OK", "State": "Enabled"},
        "Manufacturer": "Supermicro",
        "Model": "SYS-421GU-TNX",
        "BiosVersion": "1.2.3",
    }
    return client


def test_piped_non_tty_emits_json_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY stdout (piped) → JSON even when --json is omitted.

    Restores the *real* ``should_emit_json`` (the autouse conftest fixture
    replaces it with a TTY-simulating identity) so this exercises the genuine
    ``sys.stdout.isatty()`` auto-JSON path: CliRunner's captured stdout is not a
    TTY, so JSON is emitted without ``--json``.
    """
    from mcp_common.cli import should_emit_json as real_should_emit_json

    monkeypatch.setattr("redfish_mcp.cli.should_emit_json", real_should_emit_json)
    with patch("redfish_mcp.cli._client", return_value=_fake_client()):
        result = runner.invoke(app, ["health", MOCK_HOST])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)  # must parse as JSON
    assert payload["ok"] is True
    assert payload["PowerState"] == "On"


def test_tty_emits_human_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive TTY → human-readable text (not JSON) when --json is omitted."""
    monkeypatch.setattr("redfish_mcp.cli.should_emit_json", lambda explicit_json: explicit_json)
    with patch("redfish_mcp.cli._client", return_value=_fake_client()):
        result = runner.invoke(app, ["health", MOCK_HOST])
    assert result.exit_code == 0, result.output
    assert "PowerState: On" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


def test_explicit_json_flag_emits_json_at_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--json` forces JSON even at an interactive TTY."""
    monkeypatch.setattr("redfish_mcp.cli.should_emit_json", lambda explicit_json: explicit_json)
    with patch("redfish_mcp.cli._client", return_value=_fake_client()):
        result = runner.invoke(app, ["health", MOCK_HOST, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["PowerState"] == "On"
