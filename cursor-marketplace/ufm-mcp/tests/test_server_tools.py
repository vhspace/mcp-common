"""Tests for server tool functions using mocked HTTP responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from ufm_mcp.config import Settings
from ufm_mcp.site_manager import SiteManager


@pytest.fixture()
def configured_server():
    """Set up a server with a mocked UFM client."""
    import ufm_mcp.server as srv

    settings = Settings(
        ufm_url="https://ufm.example.com/",
        verify_ssl=False,
        timeout_seconds=10,
    )
    srv.sites = SiteManager()
    srv.sites.configure(settings)
    srv._base_settings = settings

    mock_client = MagicMock()
    srv.sites._clients["default"] = mock_client

    yield srv, mock_client

    srv._systems_cache.clear()
    srv.sites.close_all()


def test_ufm_list_sites(configured_server) -> None:
    srv, _ = configured_server
    result = srv.ufm_list_sites()
    assert result["ok"] is True
    assert result["active_site"] == "default"
    assert len(result["sites"]) >= 1


def test_ufm_set_site(configured_server) -> None:
    srv, _ = configured_server
    result = srv.ufm_set_site(site="default")
    assert result["ok"] is True
    assert result["active_site"] == "default"


def test_ufm_get_config(configured_server) -> None:
    srv, _ = configured_server
    result = srv.ufm_get_config()
    assert result["ok"] is True
    assert "config" in result
    assert result["config"]["resolved_site"] == "default"


def test_ufm_get_config_no_settings() -> None:
    import ufm_mcp.server as srv

    old = srv._base_settings
    srv._base_settings = None
    try:
        result = srv.ufm_get_config()
        assert result["ok"] is False
        assert "not initialized" in result["error"]
    finally:
        srv._base_settings = old


def test_ufm_get_version(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = "6.15.0"
    result = srv.ufm_get_version()
    assert result["ok"] is True
    assert result["version"] == "6.15.0"


def test_ufm_get_version_dict_response(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"version": "6.15.0", "build": "123"}
    result = srv.ufm_get_version()
    assert result["ok"] is True


def test_ufm_list_alarms_empty(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_list_alarms()
    assert result["ok"] is True
    assert result["count"] == 0


def test_ufm_list_alarms_with_data(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        [
            {"id": 1, "severity": "Warning", "name": "high_ber"},
            {"id": 2, "severity": "Critical", "name": "link_down"},
        ],
        [],
    ]
    result = srv.ufm_list_alarms()
    assert result["ok"] is True
    assert result["count"] == 2


def test_ufm_list_alarms_by_id(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"id": 1, "severity": "Warning"}
    result = srv.ufm_list_alarms(alarm_id=1)
    assert result["id"] == 1


def test_ufm_list_alarms_with_resolved_names(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        [
            {
                "id": 1,
                "severity": "Warning",
                "name": "high_ber",
                "object_name": "a088c20300f40636_1",
            },
            {"id": 2, "severity": "Critical", "name": "link_down", "object_name": "unknown_guid"},
        ],
        [
            {
                "system_guid": "a088c20300f40636",
                "system_name": "gpu-node-01",
                "ports": [{"guid": "a088c20300f40636"}],
            },
        ],
    ]
    result = srv.ufm_list_alarms(resolve_names=True)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["alarms"][0]["resolved_name"] == "gpu-node-01"
    assert "resolved_name" not in result["alarms"][1]


def test_ufm_list_alarms_without_resolved_names(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [
        {"id": 1, "severity": "Warning", "name": "high_ber", "object_name": "a088c20300f40636_1"},
    ]
    result = srv.ufm_list_alarms(resolve_names=False)
    assert result["ok"] is True
    assert "resolved_name" not in result["alarms"][0]


def test_ufm_list_alarms_limit(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [{"id": i} for i in range(100)]
    result = srv.ufm_list_alarms(limit=5)
    assert result["count"] == 5


def test_ufm_list_events_empty(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_list_events()
    assert result["ok"] is True
    assert result["count"] == 0


def test_ufm_list_events_with_filter(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [
        {"id": 1, "severity": "Warning", "group": "Fabric"},
    ]
    result = srv.ufm_list_events(severity="Warning", group="Fabric")
    assert result["ok"] is True
    assert result["count"] == 1


def test_ufm_list_unhealthy_ports(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [{"port": "p1", "reason": "high_ber"}]
    result = srv.ufm_list_unhealthy_ports()
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["unhealthy_ports"] == [{"port": "p1", "reason": "high_ber"}]
    assert "site" in result


def test_ufm_list_unhealthy_ports_empty(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_list_unhealthy_ports()
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["unhealthy_ports"] == []


def test_ufm_get_concerns(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        [{"id": 1, "severity": "Warning"}],
        [{"id": 1, "severity": "Warning", "timestamp": "2026-02-06 10:00:00"}],
    ]
    result = srv.ufm_get_concerns()
    assert result["ok"] is True
    assert "severity_summary" in result


def test_ufm_get_concerns_non_list_responses(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {"error": "unexpected"},
        {"error": "unexpected"},
    ]
    result = srv.ufm_get_concerns()
    assert result["ok"] is True
    assert "alarms_error" in result
    assert "events_error" in result


def test_ufm_get_high_ber_ports_empty(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_get_high_ber_ports()
    assert result["ok"] is True
    assert result["count"] == 0


def test_ufm_get_high_ber_ports_with_fields(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [
        {"name": "p1", "severity": "Warning", "system_name": "sw1", "extra": "data"},
    ]
    result = srv.ufm_get_high_ber_ports(fields=["name", "severity"])
    assert result["ok"] is True
    assert result["count"] == 1
    assert "extra" not in result["ports"][0]


def test_ufm_check_high_ber_recent_no_ports(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_check_high_ber_recent()
    assert result["ok"] is True
    assert result["high_ber_ports_current_count"] == 0


def test_ufm_check_high_ber_recent_with_data(configured_server) -> None:
    srv, mock_client = configured_server
    now = datetime.now(UTC)
    recent_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    mock_client.get_json.side_effect = [
        [{"name": "port1", "high_ber_severity": "warning", "system_name": "sw1", "dname": "1"}],
        [
            {
                "id": 1,
                "severity": "Warning",
                "object_name": "port1",
                "timestamp": recent_ts,
                "name": "high_ber",
            }
        ],
        [
            {
                "id": 1,
                "severity": "Warning",
                "object_name": "port1",
                "timestamp": recent_ts,
                "name": "ber_alarm",
                "description": "BER exceeded",
            }
        ],
    ]
    result = srv.ufm_check_high_ber_recent(lookback_minutes=30)
    assert result["ok"] is True
    assert result["high_ber_ports_current_count"] == 1


def test_ufm_get_log(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"content": "line1\nline2\nline3"}
    result = srv.ufm_get_log(log_type="UFM", length=100)
    assert result["ok"] is True
    assert "line1" in result["content"]


def test_ufm_get_log_non_content_response(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"status": "no content field"}
    result = srv.ufm_get_log()
    assert result["ok"] is True
    assert "response" in result


def test_ufm_search_log(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"content": "error found\nall good\nerror again"}
    result = srv.ufm_search_log(query="error", log_type="UFM")
    assert result["ok"] is True
    assert result["match_count"] == 2


def test_ufm_search_log_empty_query(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="non-empty"):
        srv.ufm_search_log(query="   ")


def test_ufm_search_logs_regex(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {
        "content": "ERR 0011 join error\nINFO normal\nERR 0012 other"
    }
    result = srv.ufm_search_logs(query=r"ERR \d{4}", regex=True, log_types=["UFM"])
    assert result["ok"] is True
    assert result["match_count"] == 2


def test_ufm_search_logs_invalid_regex(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Invalid regex"):
        srv.ufm_search_logs(query="[invalid", regex=True)


def test_ufm_create_log_history_requires_allow_write(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="allow_write"):
        srv.ufm_create_log_history(allow_write=False)


def test_ufm_create_log_history(configured_server) -> None:
    srv, mock_client = configured_server
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/42"}
    mock_client.post_no_body.return_value = mock_resp
    result = srv.ufm_create_log_history(allow_write=True)
    assert result["ok"] is True
    assert result["location"] == "/ufmRestV3/jobs/42"


def test_ufm_create_system_dump_requires_allow_write(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="allow_write"):
        srv.ufm_create_system_dump(allow_write=False)


def test_ufm_create_system_dump(configured_server) -> None:
    srv, mock_client = configured_server
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/99"}
    mock_client.post_no_body.return_value = mock_resp
    result = srv.ufm_create_system_dump(allow_write=True, mode="SnapShot")
    assert result["ok"] is True
    assert result["job_id"] == 99


def test_ufm_get_job_by_id(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"id": 42, "status": "completed"}
    result = srv.ufm_get_job(job_id=42)
    assert result["id"] == 42


def test_ufm_get_job_by_path(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"id": 42, "status": "running"}
    result = srv.ufm_get_job(job_path="/ufmRestV3/jobs/42")
    assert result["id"] == 42


def test_ufm_get_job_no_args(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="job_id or job_path"):
        srv.ufm_get_job()


@pytest.mark.anyio
async def test_ufm_download_log_history_file(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_text.return_value = "log line 1\nlog line 2\n"
    result = await srv.ufm_download_log_history_file(file_name="history_2026.txt")
    assert result["ok"] is True
    assert "log line 1" in result["content"]


@pytest.mark.anyio
async def test_ufm_download_log_history_file_empty_name(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="non-empty"):
        await srv.ufm_download_log_history_file(file_name="   ")


def test_ufm_list_pkeys(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = ["0x1", "0x7fff"]
    result = srv.ufm_list_pkeys()
    assert result["ok"] is True
    assert result["count"] == 2


def test_ufm_get_pkey(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"pkey": "0x1", "guids": ["0xaaa"]}
    result = srv.ufm_get_pkey(pkey="0x1")
    assert result["ok"] is True
    assert result["pkey"] == "0x1"


def test_ufm_get_pkey_hosts(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {
            "pkey": "0x1",
            "guids": [
                {"guid": "0x0002c9030005f34a", "membership": "full"},
                {"guid": "0x0002c9030005f34b", "membership": "full"},
                {"guid": "0x0002c9030005f35a", "membership": "limited"},
                {"guid": "0xdeadbeef00000001", "membership": "full"},
            ],
        },
        [
            {
                "system_guid": "0x0002c9030005f340",
                "system_name": "gpu-node-01",
                "ports": [
                    {"guid": "0x0002c9030005f34a"},
                    {"guid": "0x0002c9030005f34b"},
                ],
            },
            {
                "system_guid": "0x0002c9030005f350",
                "system_name": "gpu-node-02",
                "ports": [
                    {"guid": "0x0002c9030005f35a"},
                ],
            },
        ],
    ]
    result = srv.ufm_get_pkey_hosts(pkey="0x1")
    assert result["ok"] is True
    assert result["hosts_count"] == 2
    assert result["total_guids"] == 4
    assert result["unresolved_count"] == 1

    host_names = [h["hostname"] for h in result["hosts"]]
    assert "gpu-node-01" in host_names
    assert "gpu-node-02" in host_names

    node01 = next(h for h in result["hosts"] if h["hostname"] == "gpu-node-01")
    assert node01["guid_count"] == 2
    assert node01["membership_types"] == ["full"]


def test_ufm_get_pkey_hosts_no_systems(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {"pkey": "0x1", "guids": ["0xaaa", "0xbbb"]},
        [],
    ]
    result = srv.ufm_get_pkey_hosts(pkey="0x1")
    assert result["ok"] is True
    assert result["hosts_count"] == 0
    assert result["unresolved_count"] == 2


def test_ufm_get_pkey_hosts_empty_pkey(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {"pkey": "0x1", "guids": []},
        [{"system_guid": "0xaaa", "system_name": "node01"}],
    ]
    result = srv.ufm_get_pkey_hosts(pkey="0x1")
    assert result["ok"] is True
    assert result["hosts_count"] == 0
    assert result["total_guids"] == 0


def test_ufm_add_guids_to_pkey_success(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.post_json.return_value = {"status": "ok"}
    result = srv.ufm_add_guids_to_pkey(
        pkey="0x1",
        guids=["0x0002c9030005f34a", "0x0002c9030005f34b"],
    )
    assert result["ok"] is True
    assert result["guids_added"] == 2


def test_ufm_add_guids_to_pkey_http_500(configured_server) -> None:
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(500, text="GUID not found in topology")
    mock_client.post_json.side_effect = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=response
    )
    result = srv.ufm_add_guids_to_pkey(
        pkey="0x1",
        guids=["0xdeadbeef"],
    )
    assert result["ok"] is False
    assert "500" in result["error"]
    assert "hint" in result


def test_ufm_remove_guids_from_pkey_success(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.delete_json.return_value = {"status": "ok"}
    result = srv.ufm_remove_guids_from_pkey(
        pkey="0x1",
        guids=["0x0002c9030005f34a"],
    )
    assert result["ok"] is True
    assert result["guids_removed"] == 1


def test_ufm_remove_guids_from_pkey_http_500(configured_server) -> None:
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(500, text="GUID not a member")
    mock_client.delete_json.side_effect = httpx.HTTPStatusError(
        "Server error",
        request=httpx.Request("DELETE", "https://ufm/pkeys/0x1/guids/0xbad"),
        response=response,
    )
    result = srv.ufm_remove_guids_from_pkey(
        pkey="0x1",
        guids=["0xbad"],
    )
    assert result["ok"] is False
    assert "500" in result["error"]
    assert "hint" in result


def test_ufm_remove_hosts_from_pkey_success(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.delete_json.return_value = {"status": "ok"}
    result = srv.ufm_remove_hosts_from_pkey(
        pkey="0x1",
        hosts=["node01"],
    )
    assert result["ok"] is True
    assert result["hosts_removed"] == 1


def test_ufm_remove_hosts_from_pkey_http_500(configured_server) -> None:
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(500, text="Host not found")
    mock_client.delete_json.side_effect = httpx.HTTPStatusError(
        "Server error",
        request=httpx.Request("DELETE", "https://ufm/pkeys/0x1/hosts/unknown"),
        response=response,
    )
    result = srv.ufm_remove_hosts_from_pkey(
        pkey="0x1",
        hosts=["unknown-host"],
    )
    assert result["ok"] is False
    assert "500" in result["error"]
    assert "hint" in result


def test_ufm_add_hosts_to_pkey_success(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.post_json.return_value = {"status": "ok"}
    result = srv.ufm_add_hosts_to_pkey(
        pkey="0x1",
        hosts=["node01", "node02"],
    )
    assert result["ok"] is True
    assert result["hosts_added"] == 2
    assert "note" not in result
    mock_client.post_json.assert_called_once_with(
        "/ufmRestV3/resources/pkeys",
        json_body={
            "pkey": "0x1",
            "hosts_names": "node01,node02",
        },
    )


def test_ufm_add_hosts_to_pkey_success_non_default_options(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.post_json.return_value = {"status": "ok"}
    result = srv.ufm_add_hosts_to_pkey(
        pkey="0x1",
        hosts=["node01"],
        membership="limited",
        ip_over_ib=False,
        index0=True,
    )
    assert result["ok"] is True
    mock_client.post_json.assert_called_once_with(
        "/ufmRestV3/resources/pkeys",
        json_body={
            "pkey": "0x1",
            "hosts_names": "node01",
            "membership": "limited",
            "ip_over_ib": False,
            "index0": True,
        },
    )


def test_ufm_add_hosts_to_pkey_http_500(configured_server) -> None:
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(500, text="Host not found in topology")
    mock_client.post_json.side_effect = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=response
    )
    result = srv.ufm_add_hosts_to_pkey(
        pkey="0x1",
        hosts=["unknown-host"],
    )
    assert result["ok"] is False
    assert "500" in result["error"]
    assert "hint" in result
    assert result["error_phase"] == "first_request"
    assert "discovered" in result["hint"].lower()
    assert mock_client.post_json.call_count == 1


def test_ufm_add_hosts_to_pkey_http_500_json_detail(configured_server) -> None:
    """When UFM returns a JSON error body, detail should be parsed."""
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(
        500,
        json={"error": "hostname resolution failed", "hosts": ["bad-host"]},
    )
    mock_client.post_json.side_effect = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=response
    )
    result = srv.ufm_add_hosts_to_pkey(
        pkey="0x1",
        hosts=["bad-host"],
    )
    assert result["ok"] is False
    assert isinstance(result["detail"], dict)
    assert result["detail"]["error"] == "hostname resolution failed"
    assert result["error_phase"] == "first_request"
    assert mock_client.post_json.call_count == 1


def test_ufm_add_hosts_to_pkey_guid_fallback_on_additional_properties(configured_server) -> None:
    """On additionalProperties error, resolve hostnames to GUIDs and retry (#26)."""
    import httpx

    srv, mock_client = configured_server
    err_response = httpx.Response(
        500,
        json={
            "detail": "must NOT have additional properties",
            "errors": [{"field": "additionalProperties"}],
        },
    )
    err = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=err_response
    )
    mock_client.post_json.side_effect = [err, {"status": "ok"}]
    mock_client.get_json.return_value = [
        {
            "system_guid": "0x0002c9030005f340",
            "system_name": "node01",
            "ports": [{"guid": "0x0002c9030005f34a"}],
        },
    ]
    result = srv.ufm_add_hosts_to_pkey(pkey="0x1", hosts=["node01"])
    assert result["ok"] is True
    assert result["hosts_added"] == 1
    assert result["guids_added"] >= 1
    assert result["fallback_used"] == "guid"
    assert "note" in result
    assert "GUID" in result["note"]
    assert mock_client.post_json.call_count == 2
    second = mock_client.post_json.call_args_list[1]
    assert "guids" in second.kwargs["json_body"]


def test_ufm_add_hosts_to_pkey_guid_fallback_on_400_additional_properties(
    configured_server,
) -> None:
    """GUID fallback triggers for additionalProperties regardless of HTTP status code."""
    import httpx

    srv, mock_client = configured_server
    err_response = httpx.Response(
        400,
        json={"detail": "must NOT have additional properties"},
    )
    err = httpx.HTTPStatusError(
        "Bad request", request=httpx.Request("POST", "https://ufm/pkeys"), response=err_response
    )
    mock_client.post_json.side_effect = [err, {"status": "ok"}]
    mock_client.get_json.return_value = [
        {"system_guid": "0xaaa", "system_name": "n1", "ports": [{"guid": "0xaaa1"}]},
    ]
    result = srv.ufm_add_hosts_to_pkey(pkey="0x1", hosts=["n1"])
    assert result["ok"] is True
    assert result["fallback_used"] == "guid"
    assert mock_client.post_json.call_count == 2


def test_ufm_add_hosts_to_pkey_guid_fallback_no_guids_resolved(configured_server) -> None:
    """GUID fallback fails when hostnames can't be resolved to any GUIDs."""
    import httpx

    srv, mock_client = configured_server
    err_response = httpx.Response(500, json={"message": "additionalProperties not allowed"})
    err = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=err_response
    )
    mock_client.post_json.side_effect = err
    mock_client.get_json.return_value = []
    result = srv.ufm_add_hosts_to_pkey(pkey="0x1", hosts=["unknown-host"])
    assert result["ok"] is False
    assert result["error_phase"] == "guid_fallback"
    assert "unknown-host" in result["unresolved_hosts"]


def test_ufm_add_hosts_to_pkey_no_retry_on_unrelated_error(configured_server) -> None:
    """Only additionalProperties-like failures trigger the GUID fallback."""
    import httpx

    srv, mock_client = configured_server
    response = httpx.Response(400, json={"detail": "invalid pkey format"})
    mock_client.post_json.side_effect = httpx.HTTPStatusError(
        "Bad request", request=httpx.Request("POST", "https://ufm/pkeys"), response=response
    )
    result = srv.ufm_add_hosts_to_pkey(pkey="bad", hosts=["h1"])
    assert result["ok"] is False
    assert result["error_phase"] == "first_request"
    assert mock_client.post_json.call_count == 1


def test_ufm_add_hosts_to_pkey_guid_fallback_post_also_fails(configured_server) -> None:
    """If the GUID-based POST also fails, return error with guid_fallback phase."""
    import httpx

    srv, mock_client = configured_server
    r1 = httpx.Response(500, json={"message": "'additionalProperties'"})
    r2 = httpx.Response(500, text="still bad")
    e1 = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=r1
    )
    e2 = httpx.HTTPStatusError(
        "Server error", request=httpx.Request("POST", "https://ufm/pkeys"), response=r2
    )
    mock_client.post_json.side_effect = [e1, e2]
    mock_client.get_json.return_value = [
        {"system_guid": "0xaaa", "system_name": "h1", "ports": [{"guid": "0xaaa1"}]},
    ]
    result = srv.ufm_add_hosts_to_pkey(pkey="0x1", hosts=["h1"])
    assert result["ok"] is False
    assert "500" in result["error"]
    assert result["error_phase"] == "guid_fallback"
    assert mock_client.post_json.call_count == 2


def test_ufm_pkey_diff(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {
            "pkey": "0x1",
            "guids": [
                {"guid": "0x0002c9030005f34a", "membership": "full"},
                {"guid": "0x0002c9030005f35a", "membership": "full"},
            ],
        },
        [
            {
                "system_guid": "0x0002c9030005f340",
                "system_name": "node01",
                "ports": [{"guid": "0x0002c9030005f34a"}],
            },
            {
                "system_guid": "0x0002c9030005f350",
                "system_name": "node02",
                "ports": [{"guid": "0x0002c9030005f35a"}],
            },
        ],
    ]
    result = srv.ufm_pkey_diff(pkey="0x1", expected_hosts=["node01", "node03"])
    assert result["ok"] is True
    assert result["to_add"] == ["node03"]
    assert result["to_remove"] == ["node02"]
    assert result["unchanged"] == ["node01"]
    assert result["to_add_count"] == 1
    assert result["to_remove_count"] == 1
    assert result["unchanged_count"] == 1


def test_ufm_pkey_diff_in_sync(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.side_effect = [
        {"pkey": "0x1", "guids": [{"guid": "0xaaa", "membership": "full"}]},
        [{"system_guid": "0xaaa", "system_name": "node01"}],
    ]
    result = srv.ufm_pkey_diff(pkey="0x1", expected_hosts=["node01"])
    assert result["ok"] is True
    assert result["to_add"] == []
    assert result["to_remove"] == []
    assert result["unchanged"] == ["node01"]


def test_ufm_check_links_recent(configured_server) -> None:
    srv, mock_client = configured_server
    now = datetime.now(UTC)
    recent_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    mock_client.get_json.side_effect = [
        [
            {"severity": "Info", "name": "link1"},
            {"severity": "Warning", "name": "link2", "source_guid": "g1"},
        ],
        [
            {
                "id": 1,
                "severity": "Warning",
                "name": "link_down",
                "timestamp": recent_ts,
                "type": "Link",
            },
        ],
        [
            {
                "id": 1,
                "severity": "Warning",
                "name": "link_alarm",
                "timestamp": recent_ts,
                "type": "Link",
                "description": "link issue",
            },
        ],
    ]
    result = srv.ufm_check_links_recent(lookback_minutes=30)
    assert result["ok"] is True
    assert result["links"]["total_links"] == 2
    assert result["links"]["non_info_count"] == 1


def test_parse_job_id_from_location() -> None:
    from ufm_mcp.server import _parse_job_id_from_location

    assert _parse_job_id_from_location("/ufmRestV3/jobs/42") == 42
    assert _parse_job_id_from_location("/ufmRestV3/jobs/999") == 999
    assert _parse_job_id_from_location(None) is None
    assert _parse_job_id_from_location("/no/jobs/here") is None
    assert _parse_job_id_from_location("") is None


def test_extract_file_name_from_summary() -> None:
    from ufm_mcp.server import _extract_file_name_from_summary

    assert (
        _extract_file_name_from_summary("File saved to /logs/history_2026.txt")
        == "/logs/history_2026.txt"
    )
    assert _extract_file_name_from_summary("Output: events.csv ready") == "events.csv"
    assert _extract_file_name_from_summary("dump.tar.gz created") == "dump.tar.gz"
    assert _extract_file_name_from_summary("No file here") is None
    assert _extract_file_name_from_summary("") is None


@pytest.mark.anyio
async def test_ufm_create_and_wait_log_history_requires_allow_write(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="allow_write"):
        await srv.ufm_create_and_wait_log_history(allow_write=False)


@pytest.mark.anyio
async def test_ufm_create_and_wait_log_history_success(configured_server) -> None:
    srv, mock_client = configured_server

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/42"}
    mock_client.post_no_body.return_value = mock_resp

    mock_client.get_json.side_effect = [
        {"id": 42, "Status": "Running"},
        {"id": 42, "Status": "Completed", "Summary": "File saved to /logs/history_2026.txt"},
    ]
    mock_client.get_text.return_value = "log line 1\nlog line 2"

    with patch("ufm_mcp.server.asyncio.sleep", return_value=None):
        result = await srv.ufm_create_and_wait_log_history(
            allow_write=True,
            poll_interval=1,
            timeout_seconds=30,
        )
    assert result["ok"] is True
    assert result["job_id"] == 42
    assert result["file_name"] == "/logs/history_2026.txt"
    assert "log line 1" in result["content"]


@pytest.mark.anyio
async def test_ufm_create_and_wait_log_history_no_file(configured_server) -> None:
    srv, mock_client = configured_server

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/42"}
    mock_client.post_no_body.return_value = mock_resp

    mock_client.get_json.return_value = {
        "id": 42,
        "Status": "Completed",
        "Summary": "Done, no file",
    }

    with patch("ufm_mcp.server.asyncio.sleep", return_value=None):
        result = await srv.ufm_create_and_wait_log_history(allow_write=True, poll_interval=1)
    assert result["ok"] is True
    assert result["job_id"] == 42
    assert "no downloadable file" in result["note"]


@pytest.mark.anyio
async def test_ufm_create_and_wait_log_history_job_failed(configured_server) -> None:
    srv, mock_client = configured_server
    from fastmcp.exceptions import ToolError

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/42"}
    mock_client.post_no_body.return_value = mock_resp

    mock_client.get_json.return_value = {"id": 42, "Status": "Failed"}

    with patch("ufm_mcp.server.asyncio.sleep", return_value=None):
        with pytest.raises(ToolError, match="ended with status"):
            await srv.ufm_create_and_wait_log_history(allow_write=True, poll_interval=1)


@pytest.mark.anyio
async def test_ufm_create_and_wait_system_dump_requires_allow_write(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="allow_write"):
        await srv.ufm_create_and_wait_system_dump(allow_write=False)


@pytest.mark.anyio
async def test_ufm_create_and_wait_system_dump_success(configured_server) -> None:
    srv, mock_client = configured_server

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/99"}
    mock_client.post_no_body.return_value = mock_resp

    mock_client.get_json.return_value = {
        "id": 99,
        "Status": "Completed",
        "Summary": "System dump saved to /dumps/dump_2026.tar.gz",
    }

    with patch("ufm_mcp.server.asyncio.sleep", return_value=None):
        result = await srv.ufm_create_and_wait_system_dump(
            allow_write=True,
            mode="SnapShot",
            poll_interval=1,
        )
    assert result["ok"] is True
    assert result["job_id"] == 99
    assert result["mode"] == "SnapShot"


@pytest.mark.anyio
async def test_ufm_create_and_wait_system_dump_timeout(configured_server) -> None:
    srv, mock_client = configured_server
    from fastmcp.exceptions import ToolError

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.headers = {"Location": "/ufmRestV3/jobs/99"}
    mock_client.post_no_body.return_value = mock_resp

    mock_client.get_json.return_value = {"id": 99, "Status": "Running"}

    with patch("ufm_mcp.server.asyncio.sleep", return_value=None):
        with pytest.raises(ToolError, match="timed out"):
            await srv.ufm_create_and_wait_system_dump(
                allow_write=True,
                timeout_seconds=10,
                poll_interval=5,
            )


# ================================================================
#  Port health tests (issue #32)
# ================================================================


def _mock_systems_and_ports(mock_client, ports=None):
    """Set up mock responses for systems + ports queries."""
    if ports is None:
        ports = [
            {
                "name": "port1",
                "number": 1,
                "dname": "1",
                "system_name": "sw1",
                "systemID": "aaa",
                "physical_state": "Active",
                "logical_state": "Active",
                "severity": "Info",
                "high_ber_severity": "",
                "active_speed": "HDR",
                "active_width": "4x",
                "fec_mode": "RS-FEC",
                "effective_ber": "1.2e-12",
                "port_fec_uncorrectable_block_counter": 0,
                "port_fec_correctable_block_counter": 142,
                "symbol_error_counter": 0,
                "link_down_counter": 0,
                "remote_guid": "b65c909e003500ab",
                "remote_node_desc": "research-common-h100-055 HCA-8",
                "remote_lid": 123,
            },
            {
                "name": "port2",
                "number": 2,
                "dname": "2",
                "system_name": "sw1",
                "systemID": "aaa",
                "physical_state": "Down",
                "logical_state": "Down",
                "severity": "Warning",
                "high_ber_severity": "warning",
                "active_speed": "HDR",
                "active_width": "4x",
                "fec_mode": "RS-FEC",
                "effective_ber": "3.5e-08",
                "port_fec_uncorrectable_block_counter": 17,
                "port_fec_correctable_block_counter": 90000,
                "symbol_error_counter": 5,
                "link_down_counter": 3,
                "remote_guid": "c77d010f00220044",
                "remote_node_desc": "research-common-h100-056 HCA-1",
                "remote_lid": 456,
            },
            {
                "name": "port3",
                "number": 3,
                "dname": "3",
                "system_name": "sw1",
                "systemID": "aaa",
                "physical_state": "Active",
                "logical_state": "Active",
                "severity": "Info",
                "high_ber_severity": "",
                "active_speed": "HDR",
                "active_width": "4x",
            },
        ]
    systems = [
        {
            "guid": "aaa",
            "system_guid": "aaa",
            "system_name": "sw1",
            "model": "SN4700",
            "vendor": "Mellanox",
            "severity": "Info",
            "state": "active",
            "technology": "InfiniBand",
        },
    ]
    return systems, ports


def test_ufm_get_ports_health_all_ports(configured_server) -> None:
    srv, mock_client = configured_server
    systems, ports = _mock_systems_and_ports(mock_client)
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(system="sw1", port_numbers=None, include_alarms=True)
    assert result["ok"] is True
    assert len(result["ports"]) == 3
    assert result["missing_ports"] == []


def test_ufm_get_ports_health_specific_ports(configured_server) -> None:
    srv, mock_client = configured_server
    systems, ports = _mock_systems_and_ports(mock_client)
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(system="sw1", port_numbers=[1, 3])
    assert result["ok"] is True
    assert len(result["ports"]) == 2
    assert result["ports"][0]["number"] == 1
    assert result["ports"][1]["number"] == 3


def test_ufm_get_ports_health_errors_only(configured_server) -> None:
    srv, mock_client = configured_server
    systems, ports = _mock_systems_and_ports(mock_client)
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(
        system="sw1", port_numbers=None, errors_only=True, include_alarms=True
    )
    assert result["ok"] is True
    assert len(result["ports"]) == 1
    assert result["ports"][0]["number"] == 2


def test_ufm_get_ports_health_down_only(configured_server) -> None:
    srv, mock_client = configured_server
    systems, ports = _mock_systems_and_ports(mock_client)
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(
        system="sw1", port_numbers=None, down_only=True, include_alarms=True
    )
    assert result["ok"] is True
    assert len(result["ports"]) == 1
    assert result["ports"][0]["number"] == 2


def test_ufm_get_ports_health_fec_and_remote_fields(configured_server) -> None:
    """Issue #33: FEC counters, effective BER, remote info, and error counters."""
    srv, mock_client = configured_server
    systems, ports = _mock_systems_and_ports(mock_client)
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(system="sw1", port_numbers=[1, 2], include_alarms=True)
    assert result["ok"] is True
    assert len(result["ports"]) == 2

    p1 = result["ports"][0]
    assert p1["fec_mode"] == "RS-FEC"
    assert p1["effective_ber"] == "1.2e-12"
    assert p1["fec_uncorrectable"] == 0
    assert p1["fec_correctable"] == 142
    assert p1["symbol_error_counter"] == 0
    assert p1["link_down_counter"] == 0
    assert p1["remote_guid"] == "b65c909e003500ab"
    assert p1["remote_node_desc"] == "research-common-h100-055 HCA-8"
    assert p1["remote_lid"] == 123

    p2 = result["ports"][1]
    assert p2["fec_uncorrectable"] == 17
    assert p2["fec_correctable"] == 90000
    assert p2["symbol_error_counter"] == 5
    assert p2["link_down_counter"] == 3
    assert p2["remote_guid"] == "c77d010f00220044"

    # Port 3 has no FEC data — fields should be None
    mock_client.get_json.side_effect = [systems, ports, []]
    result = srv.ufm_get_ports_health(system="sw1", port_numbers=[3], include_alarms=True)
    p3 = result["ports"][0]
    assert p3["fec_mode"] is None
    assert p3["effective_ber"] is None
    assert p3["fec_uncorrectable"] is None
    assert p3["remote_guid"] is None


# ================================================================
#  Switches tests (issue #35)
# ================================================================


def test_ufm_list_switches_empty(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []
    result = srv.ufm_list_switches()
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["switches"] == []


def test_ufm_list_switches_with_data(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [
        {
            "system_name": "ibs01",
            "guid": "aaa",
            "model": "SN4700",
            "vendor": "Mellanox",
            "state": "active",
            "severity": "Info",
            "technology": "InfiniBand",
            "ports": [{"number": 1}, {"number": 2}, {"number": 3}],
        },
        {
            "system_name": "ibs02",
            "guid": "bbb",
            "model": "SN4700",
            "vendor": "Mellanox",
            "state": "active",
            "severity": "Warning",
            "technology": "InfiniBand",
            "ports": [{"number": 1}],
        },
    ]
    result = srv.ufm_list_switches()
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["switches"][0]["system_name"] == "ibs01"
    assert result["switches"][0]["total_ports"] == 3
    assert result["switches"][1]["system_name"] == "ibs02"
    assert result["switches"][1]["total_ports"] == 1
    assert result["severity_counts"]["Info"] == 1
    assert result["severity_counts"]["Warning"] == 1


def test_ufm_list_switches_errors_only(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = [
        {
            "system_name": "ibs01",
            "guid": "aaa",
            "model": "SN4700",
            "state": "active",
            "severity": "Info",
            "ports": [],
        },
        {
            "system_name": "ibs02",
            "guid": "bbb",
            "model": "SN4700",
            "state": "active",
            "severity": "Warning",
            "ports": [],
        },
    ]
    result = srv.ufm_list_switches(errors_only=True)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["switches"][0]["system_name"] == "ibs02"


def test_ufm_list_switches_non_list_response(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = {"error": "unexpected"}
    result = srv.ufm_list_switches()
    assert result["ok"] is True
    assert result["count"] == 0


# ================================================================
#  Stale-anchor fallback tests (issue #49)
# ================================================================


def _make_systems_payload(name: str, guid: str, port_count: int) -> list[dict]:
    """Build a /resources/systems response for a single system with `port_count` declared ports."""
    return [
        {
            "system_name": name,
            "name": name,
            "guid": guid,
            "system_guid": guid,
            "model": "QM9700",
            "vendor": "Mellanox",
            "severity": "Info",
            "state": "Active",
            "technology": "InfiniBand",
            "ports": [{"number": i + 1} for i in range(port_count)],
        }
    ]


def _make_port(system_name: str, system_guid: str, number: int, port_guid: str) -> dict:
    return {
        "name": f"{port_guid}_{number}",
        "guid": port_guid,
        "number": number,
        "dname": f"Port {number}",
        "physical_state": "Active",
        "logical_state": "Active",
        "severity": "Info",
        "system_name": system_name,
        "systemID": system_guid,
        "active_speed": "ndr",
        "active_width": "4x",
    }


def test_ufm_get_ports_health_stale_anchor_falls_back_to_system_name(configured_server) -> None:
    """When ?system=<guid> returns ghost-only results, fallback fetches unfiltered + filters client-side."""
    srv, mock_client = configured_server
    name = "b65c909e-16"
    stale_guid = "aaaa1111aaaa1111"
    fresh_node_guid = "bbbb2222bbbb2222"
    other_name = "unrelated-host-99"
    other_guid = "eeeeffffeeeeffff"

    systems = _make_systems_payload(name, stale_guid, port_count=7)

    # GUID query → 1 ghost port (under stale_guid).
    ghost_ports = [_make_port(name, stale_guid, 1, "0xghostghostghost")]
    # Real ports for b65c909e-16 (7 ports under fresh node guid).
    real_ports = [_make_port(name, fresh_node_guid, i, f"0xrealhca{i:02d}") for i in range(1, 8)]
    # Noise: ports from an unrelated host that must NOT be included in results.
    noise_ports = [_make_port(other_name, other_guid, i, f"0xnoise{i:02d}") for i in range(1, 4)]
    # Unfiltered payload seen by the fallback (all_ports contains real + noise).
    all_ports_unfiltered = real_ports + noise_ports

    mock_client.get_json.side_effect = [
        systems,  # /resources/systems
        ghost_ports,  # /resources/ports?system=<stale_guid>
        all_ports_unfiltered,  # /resources/ports (unfiltered fallback)
        [],  # peer-port resolution (no peers)
        [],  # alarms
    ]

    result = srv.ufm_get_ports_health(system=name, include_peer_ports=False, include_alarms=False)

    assert result["ok"] is True
    assert len(result["ports"]) == 7  # only the 7 real ports for this system_name
    assert "inventory_warnings" in result
    iw = result["inventory_warnings"]
    assert iw["stale_anchor_detected"] is True
    assert iw["anchor_guid"] == stale_guid
    assert iw["system_name"] == name
    assert iw["ports_by_guid"] == 1
    assert iw["ports_by_name"] == 7
    assert "0xghostghostghost_1" in iw["anchor_only_port_names"]
    assert iw["record_ports"] == 7
    assert "ufm-cli inventory-doctor" in iw["remediation_hint"]
    assert "skills/ufm-stale-inventory-recovery" in iw["remediation_hint"]


def test_ufm_get_ports_health_clean_anchor_no_warning(configured_server) -> None:
    """When ?system=<guid> returns the full set, no fallback fires and no warning is added."""
    srv, mock_client = configured_server
    name = "hci-clean-01"
    guid = "ccccdddd33334444"

    systems = _make_systems_payload(name, guid, port_count=4)
    ports = [_make_port(name, guid, i, f"0xclean{i:02d}") for i in range(1, 5)]

    mock_client.get_json.side_effect = [systems, ports, [], []]

    result = srv.ufm_get_ports_health(system=name, include_peer_ports=False, include_alarms=False)

    assert result["ok"] is True
    assert len(result["ports"]) == 4
    assert "inventory_warnings" not in result


def test_ufm_get_ports_health_stale_anchor_zero_guid_results(configured_server) -> None:
    """When ?system=<guid> returns NO ports at all, the fallback should still kick in."""
    srv, mock_client = configured_server
    name = "host-zero-anchor"
    stale_guid = "deaddeaddeaddead"
    fresh_guid = "feedfeedfeedfeed"
    other_name = "noise-host-42"
    other_guid = "1111222233334444"

    systems = _make_systems_payload(name, stale_guid, port_count=4)
    real_ports = [_make_port(name, fresh_guid, i, f"0xfresh{i:02d}") for i in range(1, 5)]
    # Noise ports from a different system — must not be counted in the result.
    noise_ports = [_make_port(other_name, other_guid, i, f"0xnoise{i:02d}") for i in range(1, 4)]
    all_ports_unfiltered = real_ports + noise_ports

    mock_client.get_json.side_effect = [
        systems,  # /resources/systems
        [],  # /resources/ports?system=<stale_guid> — empty
        all_ports_unfiltered,  # /resources/ports (unfiltered fallback)
    ]

    result = srv.ufm_get_ports_health(system=name, include_peer_ports=False, include_alarms=False)

    assert result["ok"] is True
    assert len(result["ports"]) == 4
    iw = result["inventory_warnings"]
    assert iw["ports_by_guid"] == 0
    assert iw["ports_by_name"] == 4
    assert iw["record_ports"] == 4
    assert iw["anchor_only_port_names"] == []  # nothing under the stale guid → no ghosts


def test_ufm_get_ports_health_port_guid_skips_systems_lookup(configured_server) -> None:
    """With port_guid=, fetch /resources/ports unfiltered and filter client-side. No /resources/systems call."""
    srv, mock_client = configured_server
    pg = "0xa088c20300556b96"
    sys_name = "ori-host-024"
    sys_guid = "bbbb2222bbbb2222"
    other_guid = "ccccddddccccdddd"

    port_record = _make_port(sys_name, sys_guid, 1, pg)
    # Noise ports from other systems that must be filtered out.
    noise_ports = [_make_port("other-host", other_guid, i, f"0xnoise{i:02d}") for i in range(1, 4)]
    all_ports_unfiltered = [port_record, *noise_ports]

    mock_client.get_json.side_effect = [
        all_ports_unfiltered,  # /resources/ports (unfiltered)
        [],  # peer-port resolution (no peers)
        [],  # alarms
    ]

    result = srv.ufm_get_ports_health(
        system="",
        port_guid=pg,
        include_peer_ports=False,
        include_alarms=False,
    )

    assert result["ok"] is True
    assert len(result["ports"]) == 1
    assert result["ports"][0]["name"] == f"{pg}_1"
    assert result["system"]["system_name"] == sys_name
    assert result.get("inventory_source") == "port_guid_query"

    # Verify no /resources/systems call happened.
    called_paths = [c.args[0] for c in mock_client.get_json.call_args_list]
    assert all("/resources/systems" not in p for p in called_paths), called_paths


def test_ufm_get_ports_health_node_guid_skips_systems_lookup(configured_server) -> None:
    """With node_guid=, query /resources/ports?system=<node_guid> directly."""
    srv, mock_client = configured_server
    ng = "bbbb2222bbbb2222"
    sys_name = "ori-host-024"

    ports = [_make_port(sys_name, ng, i, f"0xfresh{i:02d}") for i in range(1, 5)]
    mock_client.get_json.side_effect = [ports, [], []]

    result = srv.ufm_get_ports_health(
        system="",
        node_guid=ng,
        include_peer_ports=False,
        include_alarms=False,
    )

    assert result["ok"] is True
    assert len(result["ports"]) == 4
    assert result.get("inventory_source") == "node_guid_query"

    called_paths = [c.args[0] for c in mock_client.get_json.call_args_list]
    assert all("/resources/systems" not in p for p in called_paths), called_paths


def test_ufm_get_ports_health_requires_exactly_one_selector(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="exactly one"):
        srv.ufm_get_ports_health(system="")  # zero selectors

    with pytest.raises(ToolError, match="exactly one"):
        srv.ufm_get_ports_health(system="x", port_guid="0xfoo")  # two selectors


def test_ufm_check_ports_recent_sidedoor_skips_logs(configured_server) -> None:
    """When port_guid or node_guid is set, ufm_check_ports_recent skips log fetching."""
    srv, mock_client = configured_server
    pg = "0xa088c20300556b96"
    sys_name = "ori-host-024"
    sys_guid = "bbbb2222bbbb2222"
    other_guid = "ccccddddccccdddd"

    port_record = _make_port(sys_name, sys_guid, 1, pg)
    # Noise ports from other systems that must be filtered out.
    noise_ports = [_make_port("other-host", other_guid, i, f"0xnoise{i:02d}") for i in range(1, 4)]
    all_ports_unfiltered = [port_record, *noise_ports]

    mock_client.get_json.side_effect = [
        all_ports_unfiltered,  # /resources/ports (unfiltered)
        [],  # peer-port resolution
        [],  # alarms
    ]

    result = srv.ufm_check_ports_recent(
        system="",
        port_guid=pg,
        include_peer_ports=False,
        include_alarms=False,
    )

    assert result["ok"] is True
    assert result["logs"] == {}
    assert result["events"] == []
    assert result.get("inventory_source") == "port_guid_query"

    # Confirm no /resources/systems and no /logs* call happened.
    called = [c.args[0] for c in mock_client.get_json.call_args_list]
    assert all("/resources/systems" not in p for p in called), called
    assert all("/logs" not in p for p in called), called


# ================================================================
#  ufm_inventory_doctor tests (feature #48)
# ================================================================


def test_inventory_doctor_clean(configured_server) -> None:
    srv, mock_client = configured_server
    name = "hci-clean-01"
    guid = "ccccdddd33334444"

    # Build record ports with explicit names matching what the live queries return.
    by_name = [_make_port(name, guid, i, f"0xclean{i:02d}") for i in range(1, 5)]
    record_ports = [{"number": p["number"], "name": p["name"]} for p in by_name]

    systems = _make_systems_payload(name, guid, port_count=4)
    systems[0]["ports"] = record_ports

    # Production code now does one unfiltered fetch; derive both by_name and by_guid
    # from the same all_ports list (systemID == guid and system_name == name match).
    all_ports = list(by_name)
    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["ok"] is True
    assert result["inferred_diagnosis"] == "clean"
    assert result["counts"]["record_ports"] == 4
    assert result["counts"]["ports_by_name"] == 4
    assert result["counts"]["ports_by_guid"] == 4
    assert result["ghost_ports"] == []
    assert result["name_only_ports"] == []


def test_inventory_doctor_stale_anchor(configured_server) -> None:
    srv, mock_client = configured_server
    name = "b65c909e-16"
    stale_guid = "aaaa1111aaaa1111"
    fresh_guid = "bbbb2222bbbb2222"

    # Live host has 7 ports under the fresh node guid; UFM's anchor still points at the stale guid.
    by_name = [_make_port(name, fresh_guid, i, f"0xreal{i:02d}") for i in range(1, 8)]
    # Only 1 ghost port lives under the stale anchor.
    ghost_port = _make_port(name, stale_guid, 1, "0xghost01")
    # System record carries the stale anchor and lists ghost ports.
    record_ports = [{"number": 1, "name": "0xghost01_1"}]

    systems = _make_systems_payload(name, stale_guid, port_count=1)
    systems[0]["ports"] = record_ports

    # all_ports contains both the 7 real ports (systemID=fresh_guid) and the 1 ghost
    # (systemID=stale_guid). Client-side filter by systemID==stale_guid yields 1 for by_guid;
    # filter by system_name==name yields 8 total — but the diagnosis logic checks
    # len(guid) < len(name) which is 1 < 8 → stale_anchor.
    all_ports = [*by_name, ghost_port]
    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "stale_anchor"
    assert result["counts"]["ports_by_guid"] == 1
    assert result["counts"]["ports_by_name"] == 8  # 7 real + 1 ghost (both have system_name=name)
    assert "pcs resource restart ufm-enterprise" in result["remediation_hint"]


def test_inventory_doctor_host_node_desc_missing(configured_server) -> None:
    srv, mock_client = configured_server
    name = "host-no-desc"
    guid = "1111222233334444"

    # Host stopped reporting node_description — ports exist under the guid but system_name is absent.
    # Simulate by giving ports a different system_name so the by_name filter returns empty,
    # while systemID==guid still matches for by_guid.
    by_guid = [_make_port(name, guid, i, f"0xq{i:02d}") for i in range(1, 5)]
    record_ports = [{"number": i, "name": f"0xq{i:02d}_{i}"} for i in range(1, 5)]

    systems = _make_systems_payload(name, guid, port_count=4)
    systems[0]["ports"] = record_ports

    # all_ports contains ports with systemID=guid but system_name="UNKNOWN" (not == name),
    # so by_name filter returns [] and by_guid filter returns 4 ports.
    all_ports = []
    for p in by_guid:
        p_copy = dict(p)
        p_copy["system_name"] = "UNKNOWN"  # node_desc is gone — UFM can't map to system_name
        all_ports.append(p_copy)
    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "host_node_desc_missing"
    assert "stopped advertising node_description" in result["remediation_hint"]


def test_inventory_doctor_system_not_found(configured_server) -> None:
    srv, mock_client = configured_server
    mock_client.get_json.return_value = []  # /resources/systems returns nothing

    result = srv.ufm_inventory_doctor(system="ghost-host")
    assert result["ok"] is False
    assert "ghost-host" in result["error"]


def test_inventory_doctor_ghost_ports(configured_server) -> None:
    """System record lists ports that aren't on the host or anchored under the guid."""
    srv, mock_client = configured_server
    name = "host-with-ghosts"
    guid = "5555666677778888"

    # Live host has 4 ports; system record claims 6 (2 ghosts that no longer exist).
    by_name = [_make_port(name, guid, i, f"0xreal{i:02d}") for i in range(1, 5)]
    record_ports = [{"number": i, "name": f"0xreal{i:02d}_{i}"} for i in range(1, 5)] + [
        {"number": 5, "name": "0xghost05_5"},
        {"number": 6, "name": "0xghost06_6"},
    ]

    systems = _make_systems_payload(name, guid, port_count=6)
    systems[0]["ports"] = record_ports

    # all_ports is the same as by_name (ghosts are gone from both live views).
    all_ports = list(by_name)
    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "ghost_ports"
    assert result["ghost_ports"] == ["0xghost05_5", "0xghost06_6"]
    assert result["counts"]["record_ports"] == 6
    assert result["counts"]["ports_by_name"] == 4
    assert "no longer present on the host" in result["remediation_hint"]


def test_inventory_doctor_record_undercount(configured_server) -> None:
    """UFM system record is short: anchor agrees with live view (by_name == by_guid),
    but record lists fewer ports than the host actually has.
    Real observation: Ori b65c909e-16, record=7, by_name=8, by_guid=8."""
    srv, mock_client = configured_server
    name = "host-weird-drift"
    guid = "9999aaaabbbbcccc"

    # 4 ports each by name and by guid (counts equal — no stale_anchor),
    # but record's port set is a strict subset of by_name (2 of 4).
    # ghost_ports = record - by_name = empty (so ghost_ports branch doesn't fire).
    # name_only = by_name - record = 2 entries (so clean fails).
    # counts: by_name=4, by_guid=4 → record_undercount fires.
    by_name = [_make_port(name, guid, i, f"0xfresh{i:02d}") for i in range(1, 5)]
    record_ports = [
        {"number": 1, "name": "0xfresh01_1"},
        {"number": 2, "name": "0xfresh02_2"},
    ]

    systems = _make_systems_payload(name, guid, port_count=2)
    systems[0]["ports"] = record_ports

    # all_ports contains the 4 live ports — both by_name and by_guid filters match them all.
    all_ports = list(by_name)
    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "record_undercount"
    assert result["ghost_ports"] == []
    assert "0xfresh03_3" in result["name_only_ports"]
    assert "0xfresh04_4" in result["name_only_ports"]
    assert "missing ports that the host has live" in result["remediation_hint"]


def test_inventory_doctor_empty_system(configured_server) -> None:
    """System record exists but has no ports anywhere on the fabric."""
    srv, mock_client = configured_server
    name = "phantom-host"
    guid = "deadbeefdeadbeef"

    systems = _make_systems_payload(name, guid, port_count=0)
    systems[0]["ports"] = []
    all_ports: list[dict] = []  # unfiltered fetch returns nothing matching this host

    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["ok"] is True
    assert result["inferred_diagnosis"] == "empty_system"
    assert result["counts"]["record_ports"] == 0
    assert result["counts"]["ports_by_name"] == 0
    assert result["counts"]["ports_by_guid"] == 0
    assert "phantom entry" in result["remediation_hint"]


def test_inventory_doctor_record_ports_list_of_strings(configured_server) -> None:
    """Real UFM returns system.ports as a list of port-name strings, not dicts.
    Regression for a bug where the parser filtered them all out."""
    srv, mock_client = configured_server
    name = "real-ufm-fmt-host"
    guid = "11112222aaaabbbb"

    by_name = [_make_port(name, guid, i, f"0xport{i:02d}") for i in range(1, 5)]

    # Build a /resources/systems response where system.ports is the real-UFM
    # format: list of port-name strings. NOT a list of dicts.
    systems = _make_systems_payload(name, guid, port_count=4)
    systems[0]["ports"] = [f"0xport{i:02d}_{i}" for i in range(1, 5)]

    # The doctor uses unfiltered fetch + client-side filter (post-#63 fix).
    all_ports = list(by_name)  # all_ports unfiltered ≈ by_name in this scenario

    mock_client.get_json.side_effect = [systems, all_ports]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["ok"] is True
    assert result["counts"]["record_ports"] == 4, (
        "Real-UFM string format should yield record_ports=4, not 0 "
        "(would be 0 if the dict-only filter is still in place)"
    )
    assert result["counts"]["ports_by_name"] == 4
    assert result["counts"]["ports_by_guid"] == 4
    assert result["inferred_diagnosis"] == "clean"


# ================================================================
#  Tests for ufm_upload_ibdiagnet (#57)
# ================================================================


@pytest.fixture()
def configured_topaz_server_for_upload():
    """Set up server with topaz endpoint configured."""
    import ufm_mcp.server as srv
    from ufm_mcp.site_manager import SiteManager

    settings = Settings(
        ufm_url="https://ufm.example.com/",
        verify_ssl=False,
        timeout_seconds=10,
        topaz_endpoint="test:50051",
    )
    srv.sites = SiteManager()
    srv.sites.configure(settings)
    srv._base_settings = settings

    mock_ufm_client = MagicMock()
    srv.sites._clients["default"] = mock_ufm_client

    yield srv, mock_ufm_client

    srv.sites.close_all()


def test_ufm_upload_ibdiagnet_success(configured_topaz_server_for_upload, tmp_path) -> None:
    """Upload succeeds: returns collection_id, uploaded_bytes, ok=True."""
    srv, _ = configured_topaz_server_for_upload
    tarball = tmp_path / "ibdiagnet.tar.gz"
    tarball.write_bytes(b"FAKE_TAR_BYTES_FOR_TEST")

    mock_topaz = MagicMock()
    mock_topaz.upload_ibdiagnet.return_value = {
        "collection_id": "abc123",
        "success": True,
        "message": "imported",
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_upload_ibdiagnet(site="ori", ibdiagnet_path=str(tarball))

    assert result["collection_id"] == "abc123"
    assert result["uploaded_bytes"] == len(b"FAKE_TAR_BYTES_FOR_TEST")
    assert result["ok"] is True
    assert result["site"] == "ori"
    assert result["az_id"] == "us-south-2a"
    assert result["source_path"] == str(tarball)
    mock_topaz.close.assert_called_once()


def test_ufm_upload_ibdiagnet_missing_file(configured_topaz_server_for_upload) -> None:
    """Passing a nonexistent path raises ToolError."""
    from fastmcp.exceptions import ToolError

    srv, _ = configured_topaz_server_for_upload
    with pytest.raises(ToolError, match="File not found"):
        srv.ufm_upload_ibdiagnet(site="ori", ibdiagnet_path="/nonexistent/path/to.tar.gz")


def test_ufm_upload_ibdiagnet_empty_file(configured_topaz_server_for_upload, tmp_path) -> None:
    """Passing an empty file raises ToolError."""
    from fastmcp.exceptions import ToolError

    srv, _ = configured_topaz_server_for_upload
    empty = tmp_path / "empty.tar.gz"
    empty.write_bytes(b"")
    with pytest.raises(ToolError, match="File is empty"):
        srv.ufm_upload_ibdiagnet(site="ori", ibdiagnet_path=str(empty))
