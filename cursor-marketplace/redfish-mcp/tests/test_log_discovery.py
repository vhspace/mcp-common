"""Tests for log service discovery across Dell and Supermicro BMCs."""

import pytest
import responses

from redfish_mcp.cli import (
    _KNOWN_LOG_SERVICES,
    LOG_SERVICE_ALIASES,
    _discover_log_service,
    _enumerate_all_log_services,
    _first_manager_path,
    _resolve_alias,
)
from redfish_mcp.mcp_server import create_mcp_app
from redfish_mcp.redfish import RedfishClient


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


@pytest.fixture
def mock_host():
    return "192.168.1.100"


def _client(host: str) -> RedfishClient:
    return RedfishClient(
        host=host,
        user="admin",
        password="password",
        verify_tls=False,
        timeout_s=10,
    )


# ---------------------------------------------------------------------------
# _first_manager_path
# ---------------------------------------------------------------------------


class TestFirstManagerPath:
    @responses.activate
    def test_returns_first_manager(self, mock_host):
        responses.add(
            responses.GET,
            f"https://{mock_host}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
            status=200,
        )
        c = _client(mock_host)
        assert _first_manager_path(c) == "/redfish/v1/Managers/1"

    @responses.activate
    def test_returns_none_on_error(self, mock_host):
        responses.add(
            responses.GET,
            f"https://{mock_host}/redfish/v1/Managers",
            json={"error": "not found"},
            status=404,
        )
        c = _client(mock_host)
        assert _first_manager_path(c) is None

    @responses.activate
    def test_returns_none_when_empty(self, mock_host):
        responses.add(
            responses.GET,
            f"https://{mock_host}/redfish/v1/Managers",
            json={"Members": []},
            status=200,
        )
        c = _client(mock_host)
        assert _first_manager_path(c) is None


# ---------------------------------------------------------------------------
# _enumerate_all_log_services
# ---------------------------------------------------------------------------


class TestEnumerateAllLogServices:
    @responses.activate
    def test_enumerates_supermicro_services(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log2"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        result = _enumerate_all_log_services(c)
        assert len(result) == 2
        assert result[0] == ("Log1", "/redfish/v1/Managers/1/LogServices/Log1")
        assert result[1] == ("Log2", "/redfish/v1/Managers/1/LogServices/Log2")

    @responses.activate
    def test_includes_system_log_services(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Systems/1/LogServices/EventLog"},
                ]
            },
        )
        c = _client(mock_host)
        result = _enumerate_all_log_services(c)
        assert len(result) == 2
        names = [n for n, _ in result]
        assert "Log1" in names
        assert "EventLog" in names

    @responses.activate
    def test_deduplicates_by_odata_id(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        result = _enumerate_all_log_services(c)
        assert len(result) == 1

    @responses.activate
    def test_returns_empty_when_no_managers(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"error": "not found"},
            status=404,
        )
        c = _client(mock_host)
        assert _enumerate_all_log_services(c) == []


# ---------------------------------------------------------------------------
# _discover_log_service — Dell iDRAC fast path
# ---------------------------------------------------------------------------


class TestDiscoverLogServiceDell:
    @responses.activate
    def test_auto_discover_dell_sel(self, mock_host):
        """Dell iDRAC Sel should be found on first try."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
        )
        c = _client(mock_host)
        url, svc = _discover_log_service(c, None)
        assert svc == "Sel"
        assert url.endswith("/LogServices/Sel/Entries")

    @responses.activate
    def test_explicit_service_dell(self, mock_host):
        """Explicit --service Lclog on Dell."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog",
            json={"Id": "Lclog", "Name": "Lifecycle Log"},
        )
        c = _client(mock_host)
        url, svc = _discover_log_service(c, "Lclog")
        assert svc == "Lclog"
        assert url.endswith("/LogServices/Lclog/Entries")


# ---------------------------------------------------------------------------
# _discover_log_service — Supermicro auto-discovery
# ---------------------------------------------------------------------------


class TestDiscoverLogServiceSupermicro:
    def _mock_supermicro_base(self, mock_host):
        """Register common Supermicro mock responses."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )

    @responses.activate
    def test_auto_discover_supermicro_log1(self, mock_host):
        """On Supermicro, should discover Log1 from LogServices enumeration."""
        base = f"https://{mock_host}"
        self._mock_supermicro_base(mock_host)
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                ]
            },
        )
        c = _client(mock_host)
        url, svc = _discover_log_service(c, None)
        assert svc == "Log1"
        assert url == f"{base}/redfish/v1/Managers/1/LogServices/Log1/Entries"

    @responses.activate
    def test_prefers_sel_over_log1_when_both_available(self, mock_host):
        """If both Sel and Log1 are available, prefer Sel (higher priority)."""
        base = f"https://{mock_host}"
        self._mock_supermicro_base(mock_host)
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Sel"},
                ]
            },
        )
        c = _client(mock_host)
        _url, svc = _discover_log_service(c, None)
        assert svc == "Sel"

    @responses.activate
    def test_falls_back_to_unknown_service(self, mock_host):
        """If no known services match, use the first available."""
        base = f"https://{mock_host}"
        self._mock_supermicro_base(mock_host)
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/CustomLog"},
                ]
            },
        )
        c = _client(mock_host)
        _url, svc = _discover_log_service(c, None)
        assert svc == "CustomLog"

    @responses.activate
    def test_explicit_service_on_supermicro(self, mock_host):
        """Explicit --service Log1 on Supermicro falls back to first manager."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Log1",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/Log1",
            json={"Id": "Log1", "Name": "Maintenance Event Log"},
        )
        c = _client(mock_host)
        url, svc = _discover_log_service(c, "Log1")
        assert svc == "Log1"
        assert url == f"{base}/redfish/v1/Managers/1/LogServices/Log1/Entries"


# ---------------------------------------------------------------------------
# _discover_log_service — error cases
# ---------------------------------------------------------------------------


class TestDiscoverLogServiceErrors:
    @responses.activate
    def test_explicit_service_not_found_shows_available(self, mock_host):
        """Error message should list available services."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/BadService",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/BadService",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        with pytest.raises(RuntimeError, match=r"BadService.*not found.*Log1"):
            _discover_log_service(c, "BadService")

    @responses.activate
    def test_no_managers_found(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": []},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"error": "not found"},
            status=404,
        )
        c = _client(mock_host)
        with pytest.raises(RuntimeError, match="No log services found"):
            _discover_log_service(c, None)

    @responses.activate
    def test_empty_log_services(self, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={"Members": []},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        with pytest.raises(RuntimeError, match="No log services found"):
            _discover_log_service(c, None)


# ---------------------------------------------------------------------------
# _resolve_alias
# ---------------------------------------------------------------------------


class TestResolveAlias:
    def test_sel_resolves_to_log1_on_supermicro(self):
        assert _resolve_alias("sel", {"Log1", "Log2"}) == "Log1"

    def test_sel_resolves_to_sel_when_available(self):
        assert _resolve_alias("sel", {"Sel", "Log1"}) == "Sel"

    def test_lclog_resolves_to_log2(self):
        assert _resolve_alias("lclog", {"Log1", "Log2"}) == "Log2"

    def test_faultlist_resolves_to_log3(self):
        assert _resolve_alias("faultlist", {"Log1", "Log3"}) == "Log3"

    def test_returns_none_when_no_match(self):
        assert _resolve_alias("sel", {"CustomLog"}) is None

    def test_case_insensitive_alias_key(self):
        assert _resolve_alias("SEL", {"Sel", "Log1"}) == "Sel"
        assert _resolve_alias("Sel", {"Log1"}) == "Log1"


# ---------------------------------------------------------------------------
# _discover_log_service — alias matching
# ---------------------------------------------------------------------------


class TestDiscoverLogServiceAliasMatching:
    @responses.activate
    def test_sel_alias_finds_log1_on_supermicro(self, mock_host):
        """Requesting 'Sel' should resolve to Log1 via alias on Supermicro."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        url, svc = _discover_log_service(c, "Sel")
        assert svc == "Log1"
        assert url == f"{base}/redfish/v1/Managers/1/LogServices/Log1/Entries"

    @responses.activate
    def test_case_insensitive_match(self, mock_host):
        """Requesting 'eventlog' should match 'EventLog' case-insensitively."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/eventlog",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/eventlog",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/EventLog"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        c = _client(mock_host)
        _url, svc = _discover_log_service(c, "eventlog")
        assert svc == "EventLog"


# ---------------------------------------------------------------------------
# MCP tool: redfish_get_bmc_logs — Supermicro integration
# ---------------------------------------------------------------------------


class TestMcpGetBmcLogsSupermicro:
    @responses.activate
    @pytest.mark.anyio
    async def test_auto_discover_supermicro(self, mcp_tools, mock_host):
        """MCP tool should auto-discover Log1 on Supermicro."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/1/LogServices/Log1"},
                ]
            },
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/Log1/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-04-07T10:00:00Z",
                        "Severity": "Warning",
                        "Message": "Temperature threshold exceeded",
                    },
                    {
                        "Id": "2",
                        "Created": "2026-04-07T12:00:00Z",
                        "Severity": "OK",
                        "Message": "Temperature normal",
                    },
                ]
            },
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["log_service"] == "Log1"
        assert result["filtered_count"] == 2
        assert result["entries"][0]["created"] == "2026-04-07T12:00:00Z"
        assert result["entries"][1]["created"] == "2026-04-07T10:00:00Z"

    @responses.activate
    @pytest.mark.anyio
    async def test_explicit_log1_service(self, mcp_tools, mock_host):
        """Explicit log_service='Log1' on Supermicro."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Log1",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/Log1",
            json={"Id": "Log1", "Name": "MEL"},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/1/LogServices/Log1/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-04-07T10:00:00Z",
                        "Severity": "OK",
                        "Message": "test",
                    },
                ]
            },
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            log_service="Log1",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["log_service"] == "Log1"
        assert result["filtered_count"] == 1


# ---------------------------------------------------------------------------
# Client-side sorting
# ---------------------------------------------------------------------------


class TestClientSideSorting:
    @responses.activate
    @pytest.mark.anyio
    async def test_entries_sorted_newest_first(self, mcp_tools, mock_host):
        """Entries should be sorted by Created descending regardless of BMC order."""
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-04-01T08:00:00Z",
                        "Severity": "OK",
                        "Message": "oldest",
                    },
                    {
                        "Id": "3",
                        "Created": "2026-04-03T08:00:00Z",
                        "Severity": "OK",
                        "Message": "newest",
                    },
                    {
                        "Id": "2",
                        "Created": "2026-04-02T08:00:00Z",
                        "Severity": "OK",
                        "Message": "middle",
                    },
                ]
            },
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        timestamps = [e["created"] for e in result["entries"]]
        assert timestamps == [
            "2026-04-03T08:00:00Z",
            "2026-04-02T08:00:00Z",
            "2026-04-01T08:00:00Z",
        ]


# ---------------------------------------------------------------------------
# Known log services priority list sanity
# ---------------------------------------------------------------------------


def test_known_log_services_includes_supermicro():
    assert "Log1" in _KNOWN_LOG_SERVICES
    assert "Sel" in _KNOWN_LOG_SERVICES
    assert _KNOWN_LOG_SERVICES.index("Sel") < _KNOWN_LOG_SERVICES.index("Log1")


def test_alias_mapping_covers_common_names():
    assert "sel" in LOG_SERVICE_ALIASES
    assert "lclog" in LOG_SERVICE_ALIASES
    assert "faultlist" in LOG_SERVICE_ALIASES
    assert "Log1" in LOG_SERVICE_ALIASES["sel"]
    assert "Log2" in LOG_SERVICE_ALIASES["lclog"]
