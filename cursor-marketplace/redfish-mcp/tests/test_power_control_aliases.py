"""Tests for power-control action alias handling.

Covers:

* Canonical snake_case (``force_restart``) — canonical path.
* Redfish PascalCase spec values (``ForceRestart``) — resolved to canonical.
* Case-insensitive variants (``FORCERESTART``, ``Force_restart``).
* Unknown input raises ``InvalidActionError`` with a clear message.
"""

from __future__ import annotations

import pytest
import responses
from typer.testing import CliRunner

from redfish_mcp.cli import app
from redfish_mcp.power_actions import (
    ACTION_TO_RESET_TYPE,
    InvalidActionError,
    resolve_reset_type,
)

runner = CliRunner(mix_stderr=False)

MOCK_HOST = "192.168.1.100"

VALID_ACTIONS = tuple(ACTION_TO_RESET_TYPE)


@pytest.fixture(autouse=True)
def _redfish_creds(monkeypatch):
    """Provide credentials so the CLI doesn't short-circuit on missing env vars."""
    monkeypatch.setenv("REDFISH_USER", "admin")
    monkeypatch.setenv("REDFISH_PASSWORD", "password")


def _mock_reset_endpoint(host: str = MOCK_HOST) -> None:
    base = f"https://{host}"
    responses.add(
        responses.GET,
        f"{base}/redfish/v1/Systems",
        json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{base}/redfish/v1/Systems/1",
        json={
            "PowerState": "On",
            "Actions": {
                "#ComputerSystem.Reset": {
                    "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
                }
            },
        },
        status=200,
    )
    responses.add(
        responses.POST,
        f"{base}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={},
        status=200,
    )


class TestResolveResetType:
    def test_canonical_snake_case_passthrough(self):
        for action in VALID_ACTIONS:
            canonical, reset_type = resolve_reset_type(action)
            assert canonical == action
            assert reset_type == ACTION_TO_RESET_TYPE[action]

    def test_pascal_case_aliases(self):
        assert resolve_reset_type("ForceRestart") == ("force_restart", "ForceRestart")
        assert resolve_reset_type("ForceOff") == ("force_off", "ForceOff")
        assert resolve_reset_type("GracefulRestart") == ("restart", "GracefulRestart")
        assert resolve_reset_type("GracefulShutdown") == ("off", "GracefulShutdown")
        assert resolve_reset_type("On") == ("on", "On")
        assert resolve_reset_type("Nmi") == ("nmi", "Nmi")

    def test_case_insensitive_variants(self):
        assert resolve_reset_type("FORCERESTART")[0] == "force_restart"
        assert resolve_reset_type("Force_restart")[0] == "force_restart"
        assert resolve_reset_type("forcerestart")[0] == "force_restart"
        assert resolve_reset_type("FORCE_RESTART")[0] == "force_restart"

    def test_unknown_action_raises(self):
        with pytest.raises(InvalidActionError) as exc_info:
            resolve_reset_type("PushPowerButton")
        assert "Invalid action 'PushPowerButton'" in exc_info.value.message
        for valid in VALID_ACTIONS:
            assert valid in exc_info.value.message

    def test_garbage_input_raises(self):
        with pytest.raises(InvalidActionError) as exc_info:
            resolve_reset_type("garbage-xyz")
        assert "Invalid action 'garbage-xyz'" in exc_info.value.message
        for valid in VALID_ACTIONS:
            assert valid in exc_info.value.message


class TestPowerControlCliAliases:
    """End-to-end CLI tests using typer's CliRunner + mocked Redfish HTTP."""

    @responses.activate
    def test_canonical_force_restart_succeeds(self):
        _mock_reset_endpoint()
        result = runner.invoke(app, ["power-control", MOCK_HOST, "force_restart"])
        assert result.exit_code == 0, result.stderr
        assert "force_restart" in result.stdout
        assert "ForceRestart" in result.stdout

    @responses.activate
    def test_pascal_case_force_restart_succeeds(self):
        _mock_reset_endpoint()
        result = runner.invoke(app, ["power-control", MOCK_HOST, "ForceRestart"])
        assert result.exit_code == 0, result.stderr
        assert "force_restart" in result.stdout
        assert "ForceRestart" in result.stdout

    @responses.activate
    def test_upper_case_succeeds(self):
        _mock_reset_endpoint()
        result = runner.invoke(app, ["power-control", MOCK_HOST, "FORCERESTART"])
        assert result.exit_code == 0, result.stderr
        assert "force_restart" in result.stdout

    @responses.activate
    def test_mixed_underscore_case_succeeds(self):
        _mock_reset_endpoint()
        result = runner.invoke(app, ["power-control", MOCK_HOST, "Force_restart"])
        assert result.exit_code == 0, result.stderr
        assert "force_restart" in result.stdout

    def test_unknown_action_errors(self):
        result = runner.invoke(app, ["power-control", MOCK_HOST, "PushPowerButton"])
        assert result.exit_code == 1
        assert "Invalid action 'PushPowerButton'" in result.stderr
        for valid in VALID_ACTIONS:
            assert valid in result.stderr

    def test_garbage_input_errors(self):
        result = runner.invoke(app, ["power-control", MOCK_HOST, "garbage-xyz"])
        assert result.exit_code == 1
        assert "Invalid action 'garbage-xyz'" in result.stderr
        for valid in VALID_ACTIONS:
            assert valid in result.stderr


class TestMcpToolPowerControlAliases:
    """MCP-tool-level tests: schema has a Literal enum, but the underlying
    normalizer still accepts PascalCase aliases as a defensive fallback for
    agents that bypass client-side schema validation.
    """

    @responses.activate
    @pytest.mark.anyio
    async def test_mcp_pascal_case_accepted(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"PowerState": "On"},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{base}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            json={},
            status=200,
        )

        result = await mcp_tools["redfish_power_control"](
            host=mock_host,
            user="admin",
            password="password",
            action="ForceRestart",
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["action"] == "force_restart"
        assert result["reset_type"] == "ForceRestart"

    @pytest.mark.anyio
    async def test_mcp_unknown_action_has_helpful_error(self, mcp_tools, mock_host):
        result = await mcp_tools["redfish_power_control"](
            host=mock_host,
            user="admin",
            password="password",
            action="PushPowerButton",
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is False
        assert "PushPowerButton" in result["error"]


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    """Mirror the fixture from tests/test_mcp_tools.py for MCP-level tests."""
    from redfish_mcp.mcp_server import create_mcp_app

    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


@pytest.fixture
def mock_host():
    return "192.168.1.100"
