"""Tests for the MCP_ENFORCE_READONLY backstop on both redfish surfaces.

Regression coverage for the code-review BLOCKER on PR #78: read-only
enforcement was a no-op on both the MCP and CLI surfaces.

* MCP side: ``InstrumentedFastMCP`` runs on the classic ``mcp.server.fastmcp``
  stack (no fastmcp 3.x middleware dispatch), so ``ReadOnlyEnforcementMiddleware``
  was never invoked. The guard now lives in
  ``InstrumentedFastMCP._enforce_read_only`` (called first in ``call_tool``).
* CLI side: every tool is ``mcp_only=True`` (0 synthesized commands), so the
  builder's CLI gate never ran and the hand-written write commands had no
  ``@enforce_read_only_cli`` / ``refuse_if_read_only_blocked``.
"""

from __future__ import annotations

from typing import Any

import pytest
import responses
from fastmcp.exceptions import ToolError
from mcp_common.dual_mode import READONLY_REFUSAL_MESSAGE
from typer.testing import CliRunner

from redfish_mcp.cli import app
from redfish_mcp.mcp_server import create_mcp_app

runner = CliRunner()

MOCK_HOST = "10.0.0.1"
BASE = f"https://{MOCK_HOST}"


# --------------------------------------------------------------------------- #
# MCP surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_app(tmp_path, monkeypatch):
    """A fresh redfish MCP server with the enforce toggle reset to off."""
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    mcp, _tools = create_mcp_app()
    return mcp


def _stub_on_tool_call(mcp_app: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace the agent controller's dispatch with a sentinel-returning stub.

    Lets the "allowed" paths flow through ``call_tool`` without building a
    Redfish client or touching the network, while recording whether the tool
    body was reached.
    """
    state = {"calls": 0}

    async def _passthrough(**_kwargs: Any) -> str:
        state["calls"] += 1
        return "DISPATCHED"

    monkeypatch.setattr(mcp_app.agent_controller, "on_tool_call", _passthrough)
    return state


class TestMcpEnforcement:
    @pytest.mark.anyio
    async def test_write_tool_refused_under_enforce(self, mcp_app, monkeypatch):
        """A mutating tool raises the verbatim refusal and never dispatches."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        state = _stub_on_tool_call(mcp_app, monkeypatch)

        with pytest.raises(ToolError) as excinfo:
            await mcp_app.call_tool(
                "redfish_power_control",
                {
                    "host": MOCK_HOST,
                    "user": "admin",
                    "password": "password",
                    "action": "off",
                    "allow_write": True,
                },
            )

        # Exactly the framework refusal string — surfaced verbatim to the client
        # by the classic low-level server as CallToolResult(isError=True).
        assert str(excinfo.value) == READONLY_REFUSAL_MESSAGE
        # The guard fired before dispatch: the tool body never ran.
        assert state["calls"] == 0

    @pytest.mark.anyio
    async def test_write_tool_allowed_when_unset(self, mcp_app, monkeypatch):
        """With the toggle unset, the guard is a no-op and dispatch proceeds."""
        monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
        state = _stub_on_tool_call(mcp_app, monkeypatch)

        result = await mcp_app.call_tool(
            "redfish_power_control",
            {
                "host": MOCK_HOST,
                "user": "admin",
                "password": "password",
                "action": "off",
                "allow_write": True,
            },
        )

        assert result == "DISPATCHED"
        assert state["calls"] == 1

    @pytest.mark.anyio
    async def test_read_tool_allowed_under_enforce(self, mcp_app, monkeypatch):
        """A read-only tool dispatches normally even under enforce mode."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        state = _stub_on_tool_call(mcp_app, monkeypatch)

        result = await mcp_app.call_tool(
            "redfish_get_info",
            {
                "host": MOCK_HOST,
                "user": "admin",
                "password": "password",
                "info_types": ["system"],
            },
        )

        assert result == "DISPATCHED"
        assert state["calls"] == 1

    @pytest.mark.anyio
    async def test_enable_vs_strict_classification(self, mcp_app, monkeypatch):
        """ENABLED allows unclassified tools; strict refuses anything not read_only."""
        # An unknown tool name is UNCLASSIFIED: allowed under ENABLED ("1") ...
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        mcp_app._enforce_read_only("some_unknown_tool")  # no raise
        # ... but refused under strict.
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "strict")
        with pytest.raises(ToolError) as excinfo:
            mcp_app._enforce_read_only("some_unknown_tool")
        assert str(excinfo.value) == READONLY_REFUSAL_MESSAGE
        # A read-only tool is allowed even under strict.
        mcp_app._enforce_read_only("redfish_get_info")


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def _mock_system() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/redfish/v1/Systems",
        json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/redfish/v1/Systems/1",
        json={
            "Id": "1",
            "PowerState": "On",
            "Manufacturer": "Supermicro",
            "Model": "SYS-421GU-TNX",
            "BiosVersion": "1.2.3",
            "Status": {"State": "Enabled", "Health": "OK", "HealthRollup": "OK"},
        },
        status=200,
    )


class TestCliEnforcement:
    def test_power_control_refused_under_enforce(self, monkeypatch):
        """power-control (write) is refused before any BMC reset is issued."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        result = runner.invoke(app, ["power-control", MOCK_HOST, "off"])
        assert result.exit_code == 1
        assert READONLY_REFUSAL_MESSAGE in result.stderr

    def test_set_boot_refused_under_enforce(self, monkeypatch):
        """set-boot (write) is refused before any PATCH is issued."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--yes"])
        assert result.exit_code == 1
        assert READONLY_REFUSAL_MESSAGE in result.stderr

    def test_kvm_write_stub_refused_under_enforce(self, monkeypatch):
        """A KVM write subcommand is refused under enforce mode."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        result = runner.invoke(app, ["kvm", "send", MOCK_HOST, "Enter"])
        assert result.exit_code == 1
        assert READONLY_REFUSAL_MESSAGE in result.stderr

    def test_power_control_reaches_body_when_unset(self, monkeypatch):
        """Unset toggle: the gate is a no-op so the command body runs.

        Without creds the body stops at the creds check (exit 1) — proving the
        gate let it through (the refusal string is NOT printed).
        """
        monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
        monkeypatch.delenv("REDFISH_USER", raising=False)
        monkeypatch.delenv("REDFISH_PASSWORD", raising=False)
        result = runner.invoke(app, ["power-control", MOCK_HOST, "off"])
        assert READONLY_REFUSAL_MESSAGE not in result.stderr
        assert "REDFISH_USER" in result.stderr  # reached the creds check in the body

    def test_help_works_under_enforce(self, monkeypatch):
        """--help on a write command works under enforce mode (no refusal)."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        result = runner.invoke(app, ["power-control", "--help"])
        assert result.exit_code == 0
        assert READONLY_REFUSAL_MESSAGE not in result.output

    @responses.activate
    def test_read_command_runs_under_enforce(self, monkeypatch):
        """A read command (health) executes normally under enforce mode."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        monkeypatch.setenv("REDFISH_USER", "admin")
        monkeypatch.setenv("REDFISH_PASSWORD", "password")
        _mock_system()

        result = runner.invoke(app, ["health", MOCK_HOST, "--json"])
        assert result.exit_code == 0, result.stderr
        assert READONLY_REFUSAL_MESSAGE not in result.stderr

    @responses.activate
    def test_fixed_boot_order_read_path_allowed_under_enforce(self, monkeypatch):
        """fixed-boot-order WITHOUT --set is a read and is allowed under enforce."""
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        monkeypatch.setenv("REDFISH_USER", "admin")
        monkeypatch.setenv("REDFISH_PASSWORD", "password")
        # is_supermicro() probes the OEM endpoint; a 404 there yields a clean
        # "not Supermicro" error (exit 1) — but crucially NOT the refusal string,
        # proving the read path was not gated.
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/1",
            json={"Id": "1"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder",
            status=404,
        )
        result = runner.invoke(app, ["fixed-boot-order", MOCK_HOST])
        assert READONLY_REFUSAL_MESSAGE not in result.stderr

    def test_fixed_boot_order_write_path_refused_under_enforce(self, monkeypatch):
        """fixed-boot-order WITH --set is a write and is refused under enforce.

        The refusal fires before any client/network call, so no Redfish requests
        are made (``responses`` is not activated here; a stray request would error).
        """
        monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
        monkeypatch.setenv("REDFISH_USER", "admin")
        monkeypatch.setenv("REDFISH_PASSWORD", "password")
        result = runner.invoke(
            app, ["fixed-boot-order", MOCK_HOST, "--set", '{"FixedBootOrder": []}']
        )
        assert result.exit_code == 1
        assert READONLY_REFUSAL_MESSAGE in result.stderr
