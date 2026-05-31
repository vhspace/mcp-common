"""Tests for v2.0.0 features: health resource, completions, MCP logging, recent_hosts, poll_firmware_task."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import responses

from redfish_mcp.agent_controller import AgentController
from redfish_mcp.agent_state_store import AgentStateStore
from redfish_mcp.firmware_update import _check_redfish_task
from redfish_mcp.mcp_server import create_mcp_app


@pytest.fixture
def state_store(tmp_path: Path) -> AgentStateStore:
    return AgentStateStore(site="test", db_path=tmp_path / "test.sqlite3")


@pytest.fixture
def mcp_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    app, tools = create_mcp_app()
    return app, tools


# ==================== AgentStateStore.recent_hosts ====================


class TestRecentHosts:
    def test_empty_store(self, state_store: AgentStateStore) -> None:
        assert state_store.recent_hosts(limit=10) == []

    def test_returns_hosts_from_calls(self, state_store: AgentStateStore) -> None:
        state_store.record_tool_call(
            tool_name="redfish_get_info",
            hosts=["192.168.1.1"],
            ok=True,
            duration_ms=100,
            request_id=None,
            client_id=None,
            request_meta=None,
        )
        state_store.record_tool_call(
            tool_name="redfish_get_info",
            hosts=["192.168.1.2"],
            ok=True,
            duration_ms=100,
            request_id=None,
            client_id=None,
            request_meta=None,
        )
        hosts = state_store.recent_hosts(limit=10)
        assert "192.168.1.2" in hosts
        assert "192.168.1.1" in hosts

    def test_deduplicates(self, state_store: AgentStateStore) -> None:
        for _ in range(5):
            state_store.record_tool_call(
                tool_name="redfish_get_info",
                hosts=["192.168.1.1"],
                ok=True,
                duration_ms=100,
                request_id=None,
                client_id=None,
                request_meta=None,
            )
        hosts = state_store.recent_hosts(limit=10)
        assert hosts.count("192.168.1.1") == 1

    def test_respects_limit(self, state_store: AgentStateStore) -> None:
        for i in range(20):
            state_store.record_tool_call(
                tool_name="redfish_get_info",
                hosts=[f"host-{i}"],
                ok=True,
                duration_ms=100,
                request_id=None,
                client_id=None,
                request_meta=None,
            )
        hosts = state_store.recent_hosts(limit=5)
        assert len(hosts) <= 5


# ==================== _ctx_log ====================


class TestCtxLog:
    @pytest.mark.anyio
    async def test_ctx_log_calls_log(self) -> None:
        ctx = AsyncMock()
        ctx.log = AsyncMock()
        await AgentController._ctx_log(ctx, "info", "test message")
        ctx.log.assert_awaited_once_with("info", "test message", logger_name="redfish-mcp")

    @pytest.mark.anyio
    async def test_ctx_log_silences_errors(self) -> None:
        ctx = AsyncMock()
        ctx.log = AsyncMock(side_effect=RuntimeError("no transport"))
        await AgentController._ctx_log(ctx, "info", "test message")


# ==================== Health Resource ====================


class TestHealthResource:
    @pytest.mark.anyio
    async def test_health_resource_registered(self, mcp_app) -> None:
        from mcp_common.testing import mcp_client

        app, _tools = mcp_app
        async for client in mcp_client(app):
            resources = await client.list_resources()
            uris = [str(r.uri) for r in resources]
            assert any("health" in u for u in uris)


# ==================== Completions ====================


class TestCompletions:
    def test_hardware_db_vendors(self, mcp_app) -> None:
        """The hardware_db directory should have at least the supermicro vendor."""

        db_dir = Path(__file__).parent.parent / "hardware_db"
        if db_dir.is_dir():
            vendors = sorted(
                d.name for d in db_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
            )
            assert len(vendors) >= 1
            assert "supermicro" in vendors


# ==================== _check_redfish_task ====================


class TestCheckRedfishTask:
    @responses.activate
    def test_completed_task(self) -> None:
        from redfish_mcp.redfish import RedfishClient

        url = "https://host/redfish/v1/Tasks/1"
        responses.add(
            responses.GET,
            url,
            json={"TaskState": "Completed", "Messages": [{"Message": "Done"}]},
            status=200,
        )
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = _check_redfish_task(c, url)
        assert result["status"] == "Completed"
        assert result["message"] == "Done"

    @responses.activate
    def test_404_task(self) -> None:
        from redfish_mcp.redfish import RedfishClient

        url = "https://host/redfish/v1/Tasks/1"
        responses.add(responses.GET, url, status=404)
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = _check_redfish_task(c, url)
        assert result["status"] == "NotFound"

    @responses.activate
    def test_running_task(self) -> None:
        from redfish_mcp.redfish import RedfishClient

        url = "https://host/redfish/v1/Tasks/1"
        responses.add(
            responses.GET,
            url,
            json={"TaskState": "Running", "Messages": []},
            status=200,
        )
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = _check_redfish_task(c, url)
        assert result["status"] == "Running"

    def test_connection_error(self) -> None:
        from redfish_mcp.redfish import RedfishClient

        c = RedfishClient(
            host="unreachable-host-999.invalid",
            user="a",
            password="b",
            verify_tls=False,
            timeout_s=1,
        )
        result = _check_redfish_task(c, "https://unreachable-host-999.invalid/redfish/v1/Tasks/1")
        assert result["status"] == "PollError"
        assert "error" in result
