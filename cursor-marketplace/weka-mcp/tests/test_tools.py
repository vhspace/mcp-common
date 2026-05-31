"""Comprehensive tests for all 13 Weka MCP tools.

Every tool in weka_mcp.server is exercised through a mocked
WekaRestClient so no real Weka cluster is needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

import weka_mcp.server as srv


@pytest.fixture(autouse=True)
def mock_weka_client(monkeypatch):
    """Replace the site manager's client retrieval with a mock for all tests."""
    mock = MagicMock()
    monkeypatch.setattr(srv.sites, "get_client", lambda site=None: mock)
    monkeypatch.setattr(srv.sites, "_active_key", "default")
    yield mock


# ── 1. weka_cluster_overview ────────────────────────────────────


class TestClusterOverview:
    def test_returns_agent_friendly_summary(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.side_effect = lambda ep, **kw: {
            "cluster": {
                "name": "prod",
                "release": "4.4.4",
                "status": "OK",
                "io_status": "STARTED",
                "drives": {"active": 48},
                "hosts": {"total": 10, "active": 10},
                "capacity": {"total_bytes": 100, "used_bytes": 60},
            },
            "alerts": [{"type": "NodeDown", "severity": "MAJOR"}],
            "license": {"mode": "PayGo", "status": "VALID", "expiry_date": "2026-12-31"},
        }[ep]
        result = srv.weka_cluster_overview()
        assert result["name"] == "prod"
        assert result["io_status"] == "STARTED"
        assert result["drives"] == {"active": 48}
        assert result["active_alerts"] == 1
        assert result["alerts_by_severity"] == {"MAJOR": 1}
        assert result["license"]["mode"] == "PayGo"

    def test_handles_wrapped_cluster_response(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.side_effect = lambda ep, **kw: {
            "cluster": {"data": [{"name": "prod", "status": "OK", "io_status": "STARTED"}]},
            "alerts": {"data": []},
            "license": {"data": [{"mode": "PayGo"}]},
        }[ep]
        result = srv.weka_cluster_overview()
        assert result["name"] == "prod"
        assert result["io_status"] == "STARTED"
        assert result["active_alerts"] == 0

    def test_handles_alert_fetch_failure(self, mock_weka_client: MagicMock) -> None:
        def side_effect(ep: str, **kw: Any) -> Any:
            if ep == "alerts":
                raise ConnectionError("timeout")
            if ep == "cluster":
                return {"io_status": "STARTED", "status": "OK"}
            return {"mode": "PayGo"}

        mock_weka_client.get.side_effect = side_effect
        result = srv.weka_cluster_overview()
        assert result["active_alerts"] == 0
        assert result["io_status"] == "STARTED"

    def test_handles_license_fetch_failure(self, mock_weka_client: MagicMock) -> None:
        def side_effect(ep: str, **kw: Any) -> Any:
            if ep == "license":
                raise ConnectionError("timeout")
            if ep == "cluster":
                return {"io_status": "STARTED", "status": "OK"}
            return []

        mock_weka_client.get.side_effect = side_effect
        result = srv.weka_cluster_overview()
        assert "license" not in result
        assert result["io_status"] == "STARTED"

    def test_alert_query_uses_severity_filter(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {}
        srv.weka_cluster_overview()
        calls = mock_weka_client.get.call_args_list
        alert_call = next(c for c in calls if c.args[0] == "alerts")
        assert alert_call.kwargs["params"] == {"severity": "MAJOR,CRITICAL"}

    def test_strips_sensitive_data(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.side_effect = lambda ep, **kw: {
            "cluster": {"name": "prod", "status": "OK", "access_token": "secret123"},
            "alerts": [],
            "license": {"mode": "PayGo", "password": "hidden"},
        }[ep]
        result = srv.weka_cluster_overview()
        assert "access_token" not in result
        assert result.get("license", {}).get("password") is None


# ── 2. weka_list ────────────────────────────────────────────────


class TestWekaList:
    def test_list_containers(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "c1", "hostname": "node01"}]
        result = srv.weka_list(resource="containers")
        mock_weka_client.get.assert_called_once_with("containers", params=None)
        assert result[0]["hostname"] == "node01"

    def test_list_filesystems(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "fs1", "name": "default"}]
        result = srv.weka_list(resource="filesystems")
        mock_weka_client.get.assert_called_once_with("fileSystems", params=None)
        assert result[0]["name"] == "default"

    def test_list_with_fields_projection(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "c1", "hostname": "node01", "state": "ACTIVE", "ip": "10.0.0.1"}
        ]
        result = srv.weka_list(resource="containers", fields=["uid", "hostname"])
        assert "ip" not in result[0]
        assert result[0]["uid"] == "c1"

    def test_list_with_filters(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list(resource="alerts", filters={"severity": "CRITICAL"})
        mock_weka_client.get.assert_called_once_with("alerts", params={"severity": "CRITICAL"})

    def test_list_events_via_resource_type(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"id": "e1"}]
        result = srv.weka_list(resource="events")
        mock_weka_client.get.assert_called_once_with("events", params=None)
        assert result[0]["id"] == "e1"

    def test_invalid_resource_type_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Unknown resource type 'bogus'"):
            srv.weka_list(resource="bogus")

    def test_all_19_listable_resource_types_are_valid(self, mock_weka_client: MagicMock) -> None:
        expected = {
            "alerts",
            "alert_types",
            "alert_descriptions",
            "containers",
            "drives",
            "events",
            "failure_domains",
            "filesystem_groups",
            "filesystems",
            "interface_groups",
            "organizations",
            "processes",
            "s3_buckets",
            "servers",
            "smb_shares",
            "snapshot_policies",
            "snapshots",
            "tasks",
            "users",
        }
        assert set(srv._LISTABLE_RESOURCES.keys()) == expected

    def test_filters_with_none_values_are_stripped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list(resource="snapshots", filters={"filesystem_uid": "fs1", "status": None})
        mock_weka_client.get.assert_called_once_with("snapshots", params={"filesystem_uid": "fs1"})

    def test_filters_forwarded_as_params(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list(
            resource="events",
            filters={"severity": "MAJOR", "category": "clustering", "num_results": 10},
        )
        mock_weka_client.get.assert_called_once_with(
            "events",
            params={"severity": "MAJOR", "category": "clustering", "num_results": 10},
        )

    def test_empty_filters_sends_none_params(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list(resource="users", filters={})
        mock_weka_client.get.assert_called_once_with("users", params=None)


# ── 3. weka_get ─────────────────────────────────────────────────


class TestWekaGet:
    def test_get_container_by_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "c1", "hostname": "node01", "cores": 8}
        result = srv.weka_get(resource="containers", uid="c1")
        mock_weka_client.get.assert_called_once_with("containers/c1")
        assert result["cores"] == 8

    def test_get_filesystem_by_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "fs1", "name": "default", "used": "5TB"}
        result = srv.weka_get(resource="filesystems", uid="fs1")
        mock_weka_client.get.assert_called_once_with("fileSystems/fs1")
        assert result["name"] == "default"

    def test_get_with_fields_projection(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "c1", "hostname": "node01", "cores": 8}
        result = srv.weka_get(resource="containers", uid="c1", fields=["hostname"])
        assert "cores" not in result
        assert result["hostname"] == "node01"

    def test_invalid_resource_type_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Unknown resource type 'bogus'"):
            srv.weka_get(resource="bogus", uid="x")

    def test_all_11_gettable_types_are_valid(self, mock_weka_client: MagicMock) -> None:
        expected = {
            "containers",
            "drives",
            "failure_domains",
            "filesystem_groups",
            "filesystems",
            "organizations",
            "processes",
            "servers",
            "snapshot_policies",
            "snapshots",
            "users",
        }
        assert set(srv._GETTABLE_RESOURCES.keys()) == expected

    def test_get_snapshot_by_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "snap1", "name": "daily"}
        result = srv.weka_get(resource="snapshots", uid="snap1")
        mock_weka_client.get.assert_called_once_with("snapshots/snap1")
        assert result["name"] == "daily"


# ── 4. weka_get_events ──────────────────────────────────────────


class TestGetEvents:
    def test_basic_event_fetch(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"id": "e1", "severity": "INFO"}]
        result = srv.weka_get_events()
        mock_weka_client.get.assert_called_once_with("events", params=None)
        assert result[0]["id"] == "e1"

    def test_with_all_filter_params(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_get_events(
            severity="CRITICAL",
            category="io",
            num_results=10,
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-02T00:00:00Z",
        )
        mock_weka_client.get.assert_called_once_with(
            "events",
            params={
                "severity": "CRITICAL",
                "category": "io",
                "num_results": 10,
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-02T00:00:00Z",
            },
        )

    def test_empty_params_when_no_filters(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_get_events()
        mock_weka_client.get.assert_called_once_with("events", params=None)

    def test_partial_filters(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_get_events(severity="MAJOR", num_results=5)
        mock_weka_client.get.assert_called_once_with(
            "events", params={"severity": "MAJOR", "num_results": 5}
        )

    def test_with_fields_projection(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"id": "e1", "severity": "INFO", "message": "ok"}]
        result = srv.weka_get_events(fields=["id", "severity"])
        assert "message" not in result[0]
        assert result[0]["id"] == "e1"


# ── 5. weka_get_stats ──────────────────────────────────────────


class TestGetStats:
    def test_normal_stats(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"read_iops": 1000, "write_iops": 500}
        result = srv.weka_get_stats(realtime=False)
        mock_weka_client.get.assert_called_once_with("stats")
        assert result["read_iops"] == 1000

    def test_realtime_stats(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"read_iops": 50000, "latency_us": 120}
        result = srv.weka_get_stats(realtime=True)
        mock_weka_client.get.assert_called_once_with("stats/realtime")
        assert result["read_iops"] == 50000

    def test_with_fields(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "read_iops": 1000,
            "write_iops": 500,
            "cpu_usage": 0.45,
        }
        result = srv.weka_get_stats(fields=["cpu_usage"])
        assert "read_iops" not in result
        assert result["cpu_usage"] == 0.45


# ── 6. weka_list_quotas ────────────────────────────────────────


class TestListQuotas:
    def test_basic_quota_fetch(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"path": "/data/team1", "hard_limit": "1TB", "used": "500GB"}
        ]
        result = srv.weka_list_quotas(filesystem_uid="fs1")
        mock_weka_client.get.assert_called_once_with("fileSystems/fs1/quota")
        assert result[0]["hard_limit"] == "1TB"

    def test_with_fields(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"path": "/data/team1", "hard_limit": "1TB", "used": "500GB"}
        ]
        result = srv.weka_list_quotas(filesystem_uid="fs1", fields=["path"])
        assert "used" not in result[0]
        assert result[0]["path"] == "/data/team1"


# ── 7. weka_manage_alert ───────────────────────────────────────


class TestManageAlert:
    def test_mute_with_duration(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"status": "muted"}
        result = srv.weka_manage_alert(action="mute", alert_type="NodeDown", duration_secs=3600)
        mock_weka_client.put.assert_called_once_with("alerts/NodeDown/mute", json={"expiry": 3600})
        assert result["status"] == "muted"

    def test_unmute(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"status": "unmuted"}
        result = srv.weka_manage_alert(action="unmute", alert_type="NodeDown")
        mock_weka_client.put.assert_called_once_with("alerts/NodeDown/unmute")
        assert result["status"] == "unmuted"

    def test_mute_without_duration_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="duration_secs is required"):
            srv.weka_manage_alert(action="mute", alert_type="NodeDown")

    def test_invalid_action_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Invalid action 'snooze'"):
            srv.weka_manage_alert(action="snooze", alert_type="NodeDown")


# ── 8. weka_create_filesystem ──────────────────────────────────


class TestCreateFilesystem:
    def test_basic_create(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-new", "name": "myfs"}
        result = srv.weka_create_filesystem(name="myfs", capacity="10TB")
        assert result["uid"] == "fs-new"

    def test_create_with_tiering(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-tiered"}
        srv.weka_create_filesystem(
            name="tiered-fs", capacity="500GB", tiering={"obs_name": "s3-bucket"}
        )
        payload = mock_weka_client.post.call_args.kwargs["json"]
        assert payload["tiering"] == {"obs_name": "s3-bucket"}

    def test_verify_payload_sent_to_client(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-new"}
        srv.weka_create_filesystem(name="myfs", capacity="10TB")
        mock_weka_client.post.assert_called_once_with(
            "fileSystems",
            json={"name": "myfs", "total_capacity": 10 * 1024**4, "auth_required": True},
        )

    def test_create_with_group_and_auth_optional(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-grouped"}
        srv.weka_create_filesystem(
            name="shared-fs",
            capacity="5TB",
            group_name="ml-group",
            auth_required=False,
        )
        mock_weka_client.post.assert_called_once_with(
            "fileSystems",
            json={
                "name": "shared-fs",
                "total_capacity": 5 * 1024**4,
                "auth_required": False,
                "group_name": "ml-group",
            },
        )

    def test_create_with_ssd_and_total_capacity(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-tiered"}
        srv.weka_create_filesystem(
            name="tiered-fs",
            ssd_capacity="100GB",
            total_capacity="10TB",
        )
        mock_weka_client.post.assert_called_once_with(
            "fileSystems",
            json={
                "name": "tiered-fs",
                "auth_required": True,
                "ssd_capacity": 100 * 1024**3,
                "total_capacity": 10 * 1024**4,
            },
        )

    def test_create_requires_capacity_or_ssd_total(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Provide either capacity or total_capacity"):
            srv.weka_create_filesystem(name="no-capacity")


# ── 9. weka_delete_resource ────────────────────────────────────


class TestDeleteResource:
    def test_delete_filesystem(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_resource(resource="filesystems", uid="fs1")
        mock_weka_client.delete.assert_called_once_with("fileSystems/fs1")
        assert result["status"] == "deleted"

    def test_delete_snapshot(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_resource(resource="snapshots", uid="snap1")
        mock_weka_client.delete.assert_called_once_with("snapshots/snap1")
        assert result["status"] == "deleted"

    def test_delete_s3_ignores_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_resource(resource="s3", uid="ignored")
        mock_weka_client.delete.assert_called_once_with("s3")
        assert result["status"] == "deleted"

    def test_delete_organization(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_resource(resource="organizations", uid="org1")
        mock_weka_client.delete.assert_called_once_with("organizations/org1")
        assert result["status"] == "deleted"

    def test_invalid_resource_type_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Cannot delete resource type 'users'"):
            srv.weka_delete_resource(resource="users", uid="u1")


# ── 10. weka_create_snapshot ───────────────────────────────────


class TestCreateSnapshot:
    def test_basic_create(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "snap-new"}
        result = srv.weka_create_snapshot(filesystem_uid="fs1", name="my-snap")
        assert result["uid"] == "snap-new"
        mock_weka_client.post.assert_called_once_with(
            "snapshots",
            json={"filesystem_uid": "fs1", "name": "my-snap", "is_writable": False},
        )

    def test_writable_with_access_point(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "snap-rw"}
        srv.weka_create_snapshot(
            filesystem_uid="fs1", name="rw-snap", is_writable=True, access_point="/mnt/snap"
        )
        payload = mock_weka_client.post.call_args.kwargs["json"]
        assert payload["is_writable"] is True
        assert payload["access_point"] == "/mnt/snap"

    def test_verify_payload(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {}
        srv.weka_create_snapshot(filesystem_uid="fs1", name="s1", access_point="/data/snap")
        payload = mock_weka_client.post.call_args.kwargs["json"]
        assert payload == {
            "filesystem_uid": "fs1",
            "name": "s1",
            "is_writable": False,
            "access_point": "/data/snap",
        }


# ── 11. weka_upload_snapshot ───────────────────────────────────


class TestUploadSnapshot:
    def test_basic_upload(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"task_id": "upload-001"}
        result = srv.weka_upload_snapshot(uid="snap1", locator="s3://backup-bucket")
        assert result["task_id"] == "upload-001"

    def test_verify_endpoint_and_payload(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {}
        srv.weka_upload_snapshot(uid="snap1", locator="s3://my-bucket")
        mock_weka_client.post.assert_called_once_with(
            "snapshots/snap1/upload", json={"locator": "s3://my-bucket"}
        )


# ── 12. weka_restore_filesystem ────────────────────────────────


class TestRestoreFilesystem:
    def test_basic_restore(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fs-restored"}
        result = srv.weka_restore_filesystem(
            source_bucket="my-bucket", snapshot_name="snap-2025-01-15", new_fs_name="restored-fs"
        )
        assert result["uid"] == "fs-restored"

    def test_verify_payload(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {}
        srv.weka_restore_filesystem(
            source_bucket="bucket-1", snapshot_name="snap-01", new_fs_name="new-fs"
        )
        mock_weka_client.post.assert_called_once_with(
            "fileSystems/download",
            json={
                "source_bucket": "bucket-1",
                "snapshot_name": "snap-01",
                "new_fs_name": "new-fs",
            },
        )


# ── 13. weka_manage_s3 ─────────────────────────────────────────


class TestManageS3:
    def test_create_with_config(self, mock_weka_client: MagicMock) -> None:
        config: dict[str, Any] = {"default_fs_uid": "fs1", "port": 9000}
        mock_weka_client.post.return_value = {"status": "created"}
        result = srv.weka_manage_s3(action="create", config=config)
        mock_weka_client.post.assert_called_once_with("s3", json=config)
        assert result["status"] == "created"

    def test_update_with_config(self, mock_weka_client: MagicMock) -> None:
        config: dict[str, Any] = {"port": 9001}
        mock_weka_client.put.return_value = {"status": "updated"}
        result = srv.weka_manage_s3(action="update", config=config)
        mock_weka_client.put.assert_called_once_with("s3", json=config)
        assert result["status"] == "updated"

    def test_delete_no_config_needed(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_manage_s3(action="delete")
        mock_weka_client.delete.assert_called_once_with("s3")
        assert result["status"] == "deleted"

    def test_create_without_config_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="config is required"):
            srv.weka_manage_s3(action="create")

    def test_invalid_action_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Invalid action 'restart'"):
            srv.weka_manage_s3(action="restart")


# ── 14. weka_create_organization ────────────────────────────────


class TestCreateOrganization:
    def test_creates_org_with_quotas(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "org-new", "name": "research"}
        result = srv.weka_create_organization(
            name="research", ssd_quota_gb=500, total_quota_gb=2000
        )
        payload = mock_weka_client.post.call_args.kwargs["json"]
        assert mock_weka_client.post.call_args.args == ("organizations",)
        assert payload["name"] == "research"
        assert payload["ssd_quota"] == 500 * 1024**3
        assert payload["total_quota"] == 2000 * 1024**3
        assert payload["username"] == "research"
        assert "password" in payload
        assert result["uid"] == "org-new"


# ── 15. weka_create_user ────────────────────────────────────────


class TestCreateUser:
    def test_creates_user_with_default_role(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "user-new", "username": "alice"}
        result = srv.weka_create_user(username="alice", password="secret123")
        mock_weka_client.post.assert_called_once_with(
            "users",
            json={"username": "alice", "password": "secret123", "role": "OrgAdmin"},
        )
        assert result["uid"] == "user-new"

    def test_creates_user_with_custom_role(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "user-org"}
        srv.weka_create_user(username="bob", password="pass", role="Regular")
        mock_weka_client.post.assert_called_once_with(
            "users",
            json={"username": "bob", "password": "pass", "role": "Regular"},
        )


# ── 16. weka_create_filesystem_group ────────────────────────────


class TestCreateFilesystemGroup:
    def test_creates_fs_group(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.post.return_value = {"uid": "fsg-1", "name": "ml-data"}
        result = srv.weka_create_filesystem_group(name="ml-data")
        mock_weka_client.post.assert_called_once_with("fileSystemGroups", json={"name": "ml-data"})
        assert result["uid"] == "fsg-1"


# ── 17. weka_update_org_quota ────────────────────────────────────


class TestUpdateOrgQuota:
    def test_update_ssd_and_total_quota(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"status": "ok"}
        result = srv.weka_update_org_quota(org_uid="org1", ssd_quota="520TB", total_quota="1PB")
        mock_weka_client.put.assert_called_once_with(
            "organizations/org1/limits",
            json={"ssd_quota": 520 * 1024**4, "total_quota": 1 * 1024**5},
        )
        assert result["status"] == "ok"

    def test_update_ssd_quota_only(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"status": "ok"}
        srv.weka_update_org_quota(org_uid="org2", ssd_quota="100TB")
        payload = mock_weka_client.put.call_args.kwargs["json"]
        assert payload == {"ssd_quota": 100 * 1024**4}

    def test_update_total_quota_only(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"status": "ok"}
        srv.weka_update_org_quota(org_uid="org3", total_quota="2PB")
        payload = mock_weka_client.put.call_args.kwargs["json"]
        assert payload == {"total_quota": 2 * 1024**5}

    def test_no_quota_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="At least one of ssd_quota or total_quota"):
            srv.weka_update_org_quota(org_uid="org1")


# ── 18. weka_update_filesystem ──────────────────────────────────


class TestUpdateFilesystem:
    def test_update_total_capacity(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"uid": "fs1", "total_capacity": "500TB"}
        result = srv.weka_update_filesystem(uid="fs1", total_capacity="500TB")
        mock_weka_client.put.assert_called_once_with(
            "fileSystems/fs1",
            json={"total_capacity": "500TB"},
        )
        assert result["uid"] == "fs1"

    def test_update_with_new_name(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"uid": "fs1"}
        srv.weka_update_filesystem(uid="fs1", new_name="renamed-fs")
        mock_weka_client.put.assert_called_once_with(
            "fileSystems/fs1",
            json={"new_name": "renamed-fs"},
        )

    def test_update_with_multiple_fields(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.put.return_value = {"uid": "fs1"}
        srv.weka_update_filesystem(
            uid="fs1", total_capacity="1PB", new_name="big-fs", auth_required=False
        )
        payload = mock_weka_client.put.call_args.kwargs["json"]
        assert payload == {
            "total_capacity": "1PB",
            "new_name": "big-fs",
            "auth_required": False,
        }

    def test_no_fields_raises_tool_error(self, mock_weka_client: MagicMock) -> None:
        with pytest.raises(ToolError, match="At least one update field must be provided"):
            srv.weka_update_filesystem(uid="fs1")


# ── Helpers (guard + serialisation) ─────────────────────────────


class TestHelpers:
    def test_get_client_raises_for_unknown_site(self) -> None:
        with pytest.raises(ToolError, match="Unknown site"):
            srv.sites.resolve("nonexistent")

    def test_safe_result_serializes_and_projects(self) -> None:
        resp: dict[str, Any] = {"a": 1, "b": (2, 3), "c": "x"}
        out = srv._safe_result(resp, fields=["a", "b"])
        assert out == {"a": 1, "b": [2, 3]}

    def test_select_fields_works(self) -> None:
        obj = {"a": 1, "b": 2, "c": 3}
        assert srv._select_fields(obj, ["a", "c"]) == {"a": 1, "c": 3}
        assert srv._select_fields(obj, None) is obj

    def test_tuple_converted_to_list(self) -> None:
        result = srv._ensure_json_serializable({"items": (1, 2, 3)})
        assert result["items"] == [1, 2, 3]

    def test_unknown_type_is_stringified(self) -> None:
        from datetime import datetime

        result = srv._ensure_json_serializable({"ts": datetime(2025, 1, 15)})
        assert isinstance(result["ts"], str)

    def test_none_and_primitives_pass_through(self) -> None:
        assert srv._ensure_json_serializable(None) is None
        assert srv._ensure_json_serializable(42) == 42
        assert srv._ensure_json_serializable(3.14) == 3.14
        assert srv._ensure_json_serializable(True) is True
        assert srv._ensure_json_serializable("hello") == "hello"


# ── Dedicated read shortcut tools ───────────────────────────────


class TestListFilesystems:
    def test_returns_filesystem_list(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "fs1", "name": "data", "status": "READY"}]
        result = srv.weka_list_filesystems()
        mock_weka_client.get.assert_called_once_with("fileSystems")
        assert result[0]["name"] == "data"

    def test_with_fields(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "fs1", "name": "data", "status": "READY"}]
        result = srv.weka_list_filesystems(fields=["name"])
        assert "uid" not in result[0]
        assert result[0]["name"] == "data"


class TestGetFilesystem:
    def test_returns_single_fs(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "fs1", "name": "data"}
        result = srv.weka_get_filesystem(uid="fs1")
        mock_weka_client.get.assert_called_once_with("fileSystems/fs1")
        assert result["uid"] == "fs1"


class TestListFsGroups:
    def test_returns_groups(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "fsg1", "name": "default"}]
        result = srv.weka_list_fs_groups()
        mock_weka_client.get.assert_called_once_with("fileSystemGroups")
        assert result[0]["name"] == "default"


class TestListOrgs:
    def test_returns_orgs(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "org1", "name": "root"}]
        result = srv.weka_list_orgs()
        mock_weka_client.get.assert_called_once_with("organizations")
        assert result[0]["name"] == "root"


class TestGetOrg:
    def test_returns_single_org(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"uid": "org1", "name": "root"}
        result = srv.weka_get_org(uid="org1")
        mock_weka_client.get.assert_called_once_with("organizations/org1")
        assert result["uid"] == "org1"


class TestListUsers:
    def test_returns_users(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "u1", "username": "admin", "role": "ClusterAdmin"}
        ]
        result = srv.weka_list_users()
        mock_weka_client.get.assert_called_once_with("users")
        assert result[0]["username"] == "admin"


class TestGetClusterStatus:
    def test_returns_cluster_info(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {"name": "prod", "release": "4.4.4", "status": "OK"}
        result = srv.weka_get_cluster_status()
        mock_weka_client.get.assert_called_once_with("cluster")
        assert result["release"] == "4.4.4"


class TestListNodes:
    def test_returns_servers(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "srv1", "hostname": "node01", "status": "UP"}]
        result = srv.weka_list_nodes()
        mock_weka_client.get.assert_called_once_with("servers")
        assert result[0]["hostname"] == "node01"


class TestListDrives:
    def test_returns_drives(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": "drv1", "status": "ACTIVE", "model": "PM9A3"}]
        result = srv.weka_list_drives()
        mock_weka_client.get.assert_called_once_with("drives")
        assert result[0]["model"] == "PM9A3"


class TestListAlerts:
    def test_no_filter(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"type": "NodeDown", "severity": "MAJOR"}]
        result = srv.weka_list_alerts()
        mock_weka_client.get.assert_called_once_with("alerts", params=None)
        assert result[0]["type"] == "NodeDown"

    def test_with_severity_filter(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list_alerts(severity="CRITICAL")
        mock_weka_client.get.assert_called_once_with("alerts", params={"severity": "CRITICAL"})


class TestListEventsShortcut:
    def test_no_filter(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"id": "e1"}]
        result = srv.weka_list_events()
        mock_weka_client.get.assert_called_once_with("events", params=None)
        assert result[0]["id"] == "e1"

    def test_with_severity_and_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = []
        srv.weka_list_events(severity="MAJOR", num_results=5)
        mock_weka_client.get.assert_called_once_with(
            "events", params={"severity": "MAJOR", "num_results": 5}
        )


# ── Unwrap + sanitize helper tests ──────────────────────────────


class TestUnwrap:
    def test_unwraps_data_list(self) -> None:
        assert srv._unwrap({"data": [1, 2, 3]}) == [1, 2, 3]

    def test_unwraps_data_dict(self) -> None:
        assert srv._unwrap({"data": {"key": "val"}}) == {"key": "val"}

    def test_returns_plain_list_as_is(self) -> None:
        data = [1, 2, 3]
        assert srv._unwrap(data) is data

    def test_returns_plain_dict_without_data_key(self) -> None:
        data = {"key": "val"}
        assert srv._unwrap(data) is data

    def test_returns_non_dict_as_is(self) -> None:
        assert srv._unwrap("hello") == "hello"
        assert srv._unwrap(42) == 42


class TestSanitize:
    def test_strips_sensitive_keys(self) -> None:
        data = {"name": "prod", "access_token": "secret", "password": "hidden"}
        result = srv._sanitize(data)
        assert result == {"name": "prod"}

    def test_strips_nested_sensitive_keys(self) -> None:
        data = {"cluster": {"name": "prod", "token": "jwt123"}}
        result = srv._sanitize(data)
        assert result == {"cluster": {"name": "prod"}}

    def test_strips_from_lists(self) -> None:
        data = [{"name": "a", "secret": "x"}, {"name": "b", "api_key": "y"}]
        result = srv._sanitize(data)
        assert result == [{"name": "a"}, {"name": "b"}]

    def test_passthrough_primitives(self) -> None:
        assert srv._sanitize("hello") == "hello"
        assert srv._sanitize(42) == 42
        assert srv._sanitize(None) is None


class TestSafeResultUnwrap:
    def test_unwraps_wrapped_list(self) -> None:
        resp = {"data": [{"uid": "c1", "hostname": "n1"}]}
        result = srv._safe_result(resp)
        assert isinstance(result, list)
        assert result[0]["uid"] == "c1"

    def test_unwraps_and_projects_fields(self) -> None:
        resp = {"data": [{"uid": "c1", "hostname": "n1", "ip": "10.0.0.1"}]}
        result = srv._safe_result(resp, fields=["uid", "hostname"])
        assert "ip" not in result[0]
        assert result[0]["uid"] == "c1"

    def test_unwraps_and_limits(self) -> None:
        resp = {"data": [{"uid": f"c{i}"} for i in range(10)]}
        result = srv._safe_result(resp, limit=3)
        assert len(result) == 3

    def test_sanitizes_sensitive_data(self) -> None:
        resp = {"uid": "c1", "access_token": "secret"}
        result = srv._safe_result(resp)
        assert "access_token" not in result


# ── Wrapped response tests for list tools ────────────────────────


class TestWrappedResponses:
    def test_list_containers_with_wrapped_data(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "c1", "hostname": "n1", "status": "UP"},
            ]
        }
        result = srv.weka_list_containers()
        assert isinstance(result, list)
        assert result[0]["uid"] == "c1"

    def test_list_containers_fields_on_wrapped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "c1", "hostname": "n1", "ip": "10.0.0.1"},
            ]
        }
        result = srv.weka_list_containers(fields=["uid", "hostname"])
        assert "ip" not in result[0]

    def test_list_nodes_summary_on_wrapped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "s1", "status": "UP"},
                {"uid": "s2", "status": "UP"},
                {"uid": "s3", "status": "DOWN"},
            ]
        }
        result = srv.weka_list_nodes(summary=True)
        assert result["total_nodes"] == 3
        assert result["by_status"]["UP"] == 2
        assert result["by_status"]["DOWN"] == 1

    def test_list_drives_summary_on_wrapped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "d1", "status": "ACTIVE", "hostname": "n1"},
                {"uid": "d2", "status": "ACTIVE", "hostname": "n2"},
            ]
        }
        result = srv.weka_list_drives(summary=True)
        assert result["total_drives"] == 2
        assert result["by_status"]["ACTIVE"] == 2

    def test_list_alerts_summary_on_wrapped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"type": "NodeDown", "severity": "MAJOR", "is_muted": False},
            ]
        }
        result = srv.weka_list_alerts(summary=True)
        assert result["total_alerts"] == 1
        assert result["by_type"]["NodeDown"] == 1

    def test_generic_list_with_wrapped_data(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "c1", "hostname": "n1"},
            ]
        }
        result = srv.weka_list(resource="containers")
        assert isinstance(result, list)
        assert result[0]["uid"] == "c1"

    def test_generic_list_fields_on_wrapped(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "data": [
                {"uid": "c1", "hostname": "n1", "ip": "10.0.0.1"},
            ]
        }
        result = srv.weka_list(resource="containers", fields=["uid"])
        assert "ip" not in result[0]
        assert result[0]["uid"] == "c1"


# ── Container + process summary mode tests ──────────────────────


class TestContainerSummary:
    def test_summary_mode(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "c1", "status": "UP", "mode": "backend"},
            {"uid": "c2", "status": "UP", "mode": "backend"},
            {"uid": "c3", "status": "DOWN", "mode": "client"},
        ]
        result = srv.weka_list_containers(summary=True)
        assert result["total_containers"] == 3
        assert result["by_status"]["UP"] == 2
        assert result["by_status"]["DOWN"] == 1
        assert result["by_mode"]["backend"] == 2
        assert result["by_mode"]["client"] == 1

    def test_summary_ignores_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "c1", "status": "UP", "mode": "backend"},
            {"uid": "c2", "status": "UP", "mode": "backend"},
        ]
        result = srv.weka_list_containers(summary=True, limit=1)
        assert result["total_containers"] == 2


class TestProcessSummary:
    def test_summary_mode(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "p1", "status": "UP", "type": "COMPUTE"},
            {"uid": "p2", "status": "UP", "type": "DRIVES"},
            {"uid": "p3", "status": "DOWN", "type": "FRONTEND"},
        ]
        result = srv.weka_list_processes(summary=True)
        assert result["total_processes"] == 3
        assert result["by_status"]["UP"] == 2
        assert result["by_status"]["DOWN"] == 1
        assert result["by_type"]["COMPUTE"] == 1
        assert result["by_type"]["DRIVES"] == 1
        assert result["by_type"]["FRONTEND"] == 1


class TestGetCapacity:
    def test_combines_cluster_and_fs_data(self, mock_weka_client: MagicMock) -> None:
        def side_effect(ep: str, **kw: Any) -> Any:
            if ep == "cluster":
                return {"data": [{"capacity": {"total_bytes": 100, "used_bytes": 60}}]}
            if ep == "fileSystems":
                return {
                    "data": [
                        {
                            "uid": "fs1",
                            "name": "data",
                            "status": "READY",
                            "total_budget": 80,
                            "used_total": 50,
                            "available_total": 30,
                            "group_name": "default",
                        },
                    ]
                }
            return {}

        mock_weka_client.get.side_effect = side_effect
        result = srv.weka_get_capacity()
        assert result["cluster_capacity"]["total_bytes"] == 100
        assert result["filesystem_count"] == 1
        assert result["filesystems"][0]["name"] == "data"


class TestDeleteFilesystem:
    def test_deletes_by_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_filesystem(uid="fs1")
        mock_weka_client.delete.assert_called_once_with("fileSystems/fs1")
        assert result["status"] == "deleted"


class TestDeleteOrg:
    def test_deletes_by_uid(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.delete.return_value = {"status": "deleted"}
        result = srv.weka_delete_org(uid="org1")
        mock_weka_client.delete.assert_called_once_with("organizations/org1")
        assert result["status"] == "deleted"


# ── Limit parameter tests ──────────────────────────────────────


class TestLimitParameter:
    def test_weka_list_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"c{i}"} for i in range(10)]
        result = srv.weka_list(resource="containers", limit=3)
        assert len(result) == 3

    def test_weka_list_limit_none_returns_all(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"c{i}"} for i in range(10)]
        result = srv.weka_list(resource="containers")
        assert len(result) == 10

    def test_list_filesystems_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"fs{i}", "name": f"fs-{i}"} for i in range(5)]
        result = srv.weka_list_filesystems(limit=2)
        assert len(result) == 2

    def test_list_drives_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": f"d{i}", "status": "ACTIVE"} for i in range(20)
        ]
        result = srv.weka_list_drives(limit=5)
        assert len(result) == 5

    def test_list_alerts_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": f"a{i}", "type": "NodeDown"} for i in range(10)
        ]
        result = srv.weka_list_alerts(limit=3)
        assert len(result) == 3

    def test_list_nodes_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"s{i}", "status": "UP"} for i in range(8)]
        result = srv.weka_list_nodes(limit=4)
        assert len(result) == 4

    def test_list_events_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"id": f"e{i}"} for i in range(15)]
        result = srv.weka_list_events(limit=5)
        assert len(result) == 5

    def test_list_containers_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"c{i}"} for i in range(6)]
        result = srv.weka_list_containers(limit=2)
        assert len(result) == 2

    def test_list_processes_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"p{i}"} for i in range(10)]
        result = srv.weka_list_processes(limit=3)
        assert len(result) == 3

    def test_list_snapshots_with_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [{"uid": f"s{i}"} for i in range(8)]
        result = srv.weka_list_snapshots(limit=4)
        assert len(result) == 4


# ── Summary mode tests ──────────────────────────────────────────


class TestSummaryMode:
    def test_drives_summary(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "d1", "status": "ACTIVE", "hostname": "node01"},
            {"uid": "d2", "status": "ACTIVE", "hostname": "node01"},
            {"uid": "d3", "status": "INACTIVE", "hostname": "node02"},
            {"uid": "d4", "status": "ACTIVE", "hostname": "node02"},
        ]
        result = srv.weka_list_drives(summary=True)
        assert result["total_drives"] == 4
        assert result["by_status"]["ACTIVE"] == 3
        assert result["by_status"]["INACTIVE"] == 1
        assert result["by_host"]["node01"] == 2
        assert result["by_host"]["node02"] == 2

    def test_alerts_summary(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"type": "NodeDown", "severity": "MAJOR", "is_muted": False},
            {"type": "NodeDown", "severity": "MAJOR", "is_muted": True},
            {"type": "DriveError", "severity": "CRITICAL", "is_muted": False},
        ]
        result = srv.weka_list_alerts(summary=True)
        assert result["total_alerts"] == 3
        assert result["by_type"]["NodeDown"] == 2
        assert result["by_type"]["DriveError"] == 1
        assert result["by_severity"]["MAJOR"] == 2
        assert result["by_severity"]["CRITICAL"] == 1
        assert result["muted"] == 1

    def test_nodes_summary(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "s1", "status": "UP"},
            {"uid": "s2", "status": "UP"},
            {"uid": "s3", "status": "DOWN"},
        ]
        result = srv.weka_list_nodes(summary=True)
        assert result["total_nodes"] == 3
        assert result["by_status"]["UP"] == 2
        assert result["by_status"]["DOWN"] == 1

    def test_drives_summary_ignores_limit(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"uid": "d1", "status": "ACTIVE", "hostname": "n1"},
            {"uid": "d2", "status": "ACTIVE", "hostname": "n2"},
        ]
        result = srv.weka_list_drives(summary=True, limit=1)
        assert result["total_drives"] == 2

    def test_alerts_summary_with_severity_filter(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = [
            {"type": "NodeDown", "severity": "CRITICAL", "is_muted": False},
        ]
        result = srv.weka_list_alerts(severity="CRITICAL", summary=True)
        assert result["total_alerts"] == 1
        assert result["by_severity"]["CRITICAL"] == 1


# ── Stats compact mode tests ────────────────────────────────────


class TestStatsCompact:
    def test_compact_strips_nulls_and_zeros(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "read_iops": 1000,
            "write_iops": 0,
            "nfs_ops": None,
            "smb_ops": "",
            "cpu_usage": 0.45,
        }
        result = srv.weka_get_stats(compact=True)
        assert result["read_iops"] == 1000
        assert result["cpu_usage"] == 0.45
        assert "write_iops" not in result
        assert "nfs_ops" not in result
        assert "smb_ops" not in result

    def test_compact_false_preserves_all(self, mock_weka_client: MagicMock) -> None:
        mock_weka_client.get.return_value = {
            "read_iops": 1000,
            "write_iops": 0,
            "nfs_ops": None,
        }
        result = srv.weka_get_stats(compact=False)
        assert "write_iops" in result
        assert "nfs_ops" in result


# ── Helper function tests ────────────────────────────────────────


class TestNewHelpers:
    def test_apply_limit_with_list(self) -> None:
        assert srv._apply_limit([1, 2, 3, 4, 5], 3) == [1, 2, 3]

    def test_apply_limit_none_returns_all(self) -> None:
        data = [1, 2, 3]
        assert srv._apply_limit(data, None) is data

    def test_apply_limit_non_list_passthrough(self) -> None:
        data = {"key": "value"}
        assert srv._apply_limit(data, 5) is data

    def test_summarize_by(self) -> None:
        items = [
            {"status": "ACTIVE"},
            {"status": "ACTIVE"},
            {"status": "DOWN"},
        ]
        result = srv._summarize_by(items, "status")
        assert result == {"ACTIVE": 2, "DOWN": 1}

    def test_summarize_by_missing_key(self) -> None:
        items = [{"status": "UP"}, {"other": "val"}]
        result = srv._summarize_by(items, "status")
        assert result == {"UP": 1, "unknown": 1}

    def test_safe_result_with_limit(self) -> None:
        data = [{"a": 1}, {"a": 2}, {"a": 3}]
        result = srv._safe_result(data, fields=["a"], limit=2)
        assert len(result) == 2
        assert result[0] == {"a": 1}
