"""Comprehensive tests for all weka-cli commands.

Every CLI command is exercised through a mocked WekaRestClient
so no real Weka cluster is needed. Mirrors the approach in test_tools.py.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from weka_mcp.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def mock_client():
    """Patch _client() to return a MagicMock WekaRestClient for every test."""
    mock = MagicMock()
    with patch("weka_mcp.cli._client", return_value=mock):
        yield mock


# ── health ───────────────────────────────────────────────────────


class TestHealth:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.side_effect = lambda ep, **kw: {
            "cluster": {"name": "prod", "release": "4.4.4"},
            "alerts": [],
            "license": {"valid": True},
        }[ep]
        result = runner.invoke(app, ["health", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cluster"]["name"] == "prod"
        assert data["alerts"] == []

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.side_effect = lambda ep, **kw: {
            "cluster": {"data": [{"name": "prod", "release": "4.4.4", "status": "OK"}]},
            "alerts": {"data": []},
            "license": {"data": [{"mode": "unlimited"}]},
        }[ep]
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        assert "## Cluster" in result.output
        assert "prod" in result.output

    def test_alert_fetch_failure(self, mock_client: MagicMock) -> None:
        def side_effect(ep: str, **kw: Any) -> Any:
            if ep == "alerts":
                raise ConnectionError("timeout")
            if ep == "cluster":
                return {"data": [{"name": "prod"}]}
            return {"data": [{"mode": "test"}]}

        mock_client.get.side_effect = side_effect
        result = runner.invoke(app, ["health", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["alerts"]["error"] == "could not fetch alerts"


# ── filesystems ──────────────────────────────────────────────────


class TestFilesystems:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "fs1", "name": "data", "status": "READY"}]}
        result = runner.invoke(app, ["filesystems", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "data"

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {
                    "uid": "fs1",
                    "name": "data",
                    "status": "READY",
                    "total_capacity": "10TB",
                    "used_total": "5TB",
                }
            ]
        }
        result = runner.invoke(app, ["filesystems"])
        assert result.exit_code == 0
        assert "1 filesystem(s)" in result.output
        assert "data" in result.output

    def test_fields_option(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "fs1", "name": "data", "status": "READY"}]}
        result = runner.invoke(app, ["filesystems", "--json", "--fields", "name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "uid" not in data[0]
        assert data[0]["name"] == "data"


# ── get-filesystem ───────────────────────────────────────────────


class TestGetFilesystem:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "fs1", "name": "data", "status": "READY"}
        result = runner.invoke(app, ["get-filesystem", "fs1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "fs1"
        mock_client.get.assert_called_once_with("fileSystems/fs1")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "fs1", "name": "data"}
        result = runner.invoke(app, ["get-filesystem", "fs1"])
        assert result.exit_code == 0
        assert "fs1" in result.output

    def test_unwraps_data_list_wrapper(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "fs1", "name": "data"}]}
        result = runner.invoke(app, ["get-filesystem", "fs1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "fs1"

    def test_unwraps_data_dict_wrapper(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": {"uid": "fs1", "name": "data", "status": "READY"}}
        result = runner.invoke(app, ["get-filesystem", "fs1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "fs1"
        assert data["name"] == "data"

    def test_fields_option(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "fs1", "name": "data", "status": "READY"}
        result = runner.invoke(app, ["get-filesystem", "fs1", "--json", "-f", "name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "uid" not in data
        assert data["name"] == "data"


# ── containers ───────────────────────────────────────────────────


class TestContainers:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "c1", "hostname": "node01"}]}
        result = runner.invoke(app, ["containers", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["hostname"] == "node01"

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "c1", "hostname": "node01", "status": "UP"}]
        }
        result = runner.invoke(app, ["containers"])
        assert result.exit_code == 0
        assert "1 result(s)" in result.output

    def test_summary(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"uid": "c1", "status": "UP", "mode": "backend"},
                {"uid": "c2", "status": "UP", "mode": "client"},
            ]
        }
        result = runner.invoke(app, ["containers", "--summary", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_containers"] == 2
        assert data["by_status"]["UP"] == 2
        assert data["by_mode"]["backend"] == 1


# ── nodes ────────────────────────────────────────────────────────


class TestNodes:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "srv1", "hostname": "node01"}]}
        result = runner.invoke(app, ["nodes", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["hostname"] == "node01"
        mock_client.get.assert_called_once_with("servers")

    def test_summary(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"uid": "s1", "status": "UP"},
                {"uid": "s2", "status": "UP"},
                {"uid": "s3", "status": "DOWN"},
            ]
        }
        result = runner.invoke(app, ["nodes", "--summary", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_nodes"] == 3
        assert data["by_status"]["UP"] == 2


# ── drives ───────────────────────────────────────────────────────


class TestDrives:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "drv1", "model": "PM9A3", "status": "ACTIVE"}]
        }
        result = runner.invoke(app, ["drives", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["model"] == "PM9A3"

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "drv1", "name": "ssd0", "status": "ACTIVE"}]
        }
        result = runner.invoke(app, ["drives"])
        assert result.exit_code == 0
        assert "1 result(s)" in result.output

    def test_summary(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"uid": "d1", "status": "ACTIVE", "hostname": "n1"},
                {"uid": "d2", "status": "ACTIVE", "hostname": "n2"},
                {"uid": "d3", "status": "INACTIVE", "hostname": "n1"},
            ]
        }
        result = runner.invoke(app, ["drives", "--summary", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_drives"] == 3
        assert data["by_status"]["ACTIVE"] == 2


# ── alerts ───────────────────────────────────────────────────────


class TestAlerts:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"type": "NodeDown", "severity": "MAJOR"}]}
        result = runner.invoke(app, ["alerts", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["type"] == "NodeDown"

    def test_severity_filter(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": []}
        result = runner.invoke(app, ["alerts", "--severity", "CRITICAL", "--json"])
        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("alerts", params={"severity": "CRITICAL"})

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"type": "NodeDown", "title": "Node is down", "severity": "MAJOR"}]
        }
        result = runner.invoke(app, ["alerts"])
        assert result.exit_code == 0
        assert "1 alert(s)" in result.output
        assert "[NodeDown]" in result.output

    def test_summary(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"type": "NodeDown", "severity": "MAJOR", "is_muted": False},
                {"type": "NodeDown", "severity": "MAJOR", "is_muted": True},
                {"type": "DriveError", "severity": "CRITICAL", "is_muted": False},
            ]
        }
        result = runner.invoke(app, ["alerts", "--summary", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_alerts"] == 3
        assert data["by_type"]["NodeDown"] == 2
        assert data["muted"] == 1


# ── events ───────────────────────────────────────────────────────


class TestEvents:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"id": "e1", "severity": "INFO"}]}
        result = runner.invoke(app, ["events", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["id"] == "e1"

    def test_filters(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": []}
        result = runner.invoke(
            app,
            [
                "events",
                "--severity",
                "MAJOR",
                "--category",
                "IO",
                "--limit",
                "5",
                "--json",
            ],
        )
        assert result.exit_code == 0
        mock_client.get.assert_called_once_with(
            "events", params={"severity": "MAJOR", "category": "IO", "num_results": 5}
        )

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"timestamp": "2025-01-15T10:00:00Z", "severity": "INFO", "description": "All good"}
            ]
        }
        result = runner.invoke(app, ["events"])
        assert result.exit_code == 0
        assert "1 event(s)" in result.output
        assert "All good" in result.output


# ── stats ────────────────────────────────────────────────────────


class TestStats:
    def test_default_stats(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"read_iops": 1000, "write_iops": 500}
        result = runner.invoke(app, ["stats", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["read_iops"] == 1000
        mock_client.get.assert_called_once_with("stats")

    def test_realtime_stats(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"read_iops": 50000}
        result = runner.invoke(app, ["stats", "--realtime", "--json"])
        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("stats/realtime")


# ── snapshots ────────────────────────────────────────────────────


class TestSnapshots:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "snap1", "name": "daily"}]}
        result = runner.invoke(app, ["snapshots", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "daily"

    def test_filter_by_fs(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": []}
        result = runner.invoke(app, ["snapshots", "--fs", "fs1", "--json"])
        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("snapshots", params={"filesystem_uid": "fs1"})


# ── processes ────────────────────────────────────────────────────


class TestProcesses:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "p1", "name": "frontend", "status": "UP"}]}
        result = runner.invoke(app, ["processes", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "frontend"
        mock_client.get.assert_called_once_with("processes")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "p1", "name": "frontend", "status": "UP"}]}
        result = runner.invoke(app, ["processes"])
        assert result.exit_code == 0
        assert "1 result(s)" in result.output

    def test_summary(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [
                {"uid": "p1", "status": "UP", "type": "COMPUTE"},
                {"uid": "p2", "status": "UP", "type": "DRIVES"},
                {"uid": "p3", "status": "DOWN", "type": "FRONTEND"},
            ]
        }
        result = runner.invoke(app, ["processes", "--summary", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_processes"] == 3
        assert data["by_type"]["COMPUTE"] == 1


# ── list (generic) ───────────────────────────────────────────────


class TestListResource:
    def test_list_containers(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "c1"}]}
        result = runner.invoke(app, ["list", "containers", "--json"])
        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("containers")

    def test_list_unknown_resource(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["list", "bogus"])
        assert result.exit_code != 0

    def test_list_with_fields(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "c1", "hostname": "node01", "ip": "10.0.0.1"}]
        }
        result = runner.invoke(app, ["list", "containers", "--json", "-f", "uid,hostname"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "ip" not in data[0]


# ── get (generic) ────────────────────────────────────────────────


class TestGetResource:
    def test_get_by_uid(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "c1", "hostname": "node01"}
        result = runner.invoke(app, ["get", "containers", "c1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["hostname"] == "node01"
        mock_client.get.assert_called_once_with("containers/c1")

    def test_unknown_resource(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["get", "bogus", "x"])
        assert result.exit_code != 0


# ── orgs ─────────────────────────────────────────────────────────


class TestOrgs:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "org1", "name": "root"}]}
        result = runner.invoke(app, ["orgs", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "root"

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "org1", "name": "root", "ssd_quota": 1000}]
        }
        result = runner.invoke(app, ["orgs"])
        assert result.exit_code == 0
        assert "1 organization(s)" in result.output
        assert "root" in result.output


# ── get-org ──────────────────────────────────────────────────────


class TestGetOrg:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "org1", "name": "root"}
        result = runner.invoke(app, ["get-org", "org1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "org1"
        mock_client.get.assert_called_once_with("organizations/org1")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"uid": "org1", "name": "root"}
        result = runner.invoke(app, ["get-org", "org1"])
        assert result.exit_code == 0
        assert "org1" in result.output

    def test_unwraps_data_wrapper(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "org1", "name": "root"}]}
        result = runner.invoke(app, ["get-org", "org1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "org1"


# ── cluster-status ───────────────────────────────────────────────


class TestClusterStatus:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"name": "prod", "release": "4.4.4", "status": "OK"}
        result = runner.invoke(app, ["cluster-status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["release"] == "4.4.4"
        mock_client.get.assert_called_once_with("cluster")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"name": "prod", "release": "4.4.4"}
        result = runner.invoke(app, ["cluster-status"])
        assert result.exit_code == 0
        assert "prod" in result.output

    def test_unwraps_data_wrapper(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"name": "prod", "release": "4.4.4"}]}
        result = runner.invoke(app, ["cluster-status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "prod"

    def test_fields_option(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"name": "prod", "release": "4.4.4", "status": "OK"}
        result = runner.invoke(app, ["cluster-status", "--json", "-f", "name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"name": "prod"}


# ── quotas ───────────────────────────────────────────────────────


class TestQuotas:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"path": "/data/team1", "hard_limit": "1TB", "used": "500GB"}]
        }
        result = runner.invoke(app, ["quotas", "fs1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["hard_limit"] == "1TB"
        mock_client.get.assert_called_once_with("fileSystems/fs1/quota")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"path": "/data/team1", "hard_limit": "1TB", "used": "500GB"}]
        }
        result = runner.invoke(app, ["quotas", "fs1"])
        assert result.exit_code == 0
        assert "1 result(s)" in result.output


# ── users ────────────────────────────────────────────────────────


class TestUsers:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "u1", "username": "admin", "role": "ClusterAdmin"}]
        }
        result = runner.invoke(app, ["users", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["username"] == "admin"

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {
            "data": [{"uid": "u1", "username": "admin", "role": "ClusterAdmin", "org": "root"}]
        }
        result = runner.invoke(app, ["users"])
        assert result.exit_code == 0
        assert "1 user(s)" in result.output
        assert "admin" in result.output
        assert "role=ClusterAdmin" in result.output


# ── fs-groups ────────────────────────────────────────────────────


class TestFsGroups:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "fsg1", "name": "default"}]}
        result = runner.invoke(app, ["fs-groups", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "default"
        mock_client.get.assert_called_once_with("fileSystemGroups")


# ── capacity ─────────────────────────────────────────────────────


class TestCapacity:
    def test_json_output(self, mock_client: MagicMock) -> None:
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

        mock_client.get.side_effect = side_effect
        result = runner.invoke(app, ["capacity", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cluster_capacity"]["total_bytes"] == 100
        assert data["filesystem_count"] == 1

    def test_human_output(self, mock_client: MagicMock) -> None:
        def side_effect(ep: str, **kw: Any) -> Any:
            if ep == "cluster":
                return {"data": [{"capacity": {"total_bytes": 100}}]}
            return {"data": [{"uid": "fs1", "name": "data", "status": "READY"}]}

        mock_client.get.side_effect = side_effect
        result = runner.invoke(app, ["capacity"])
        assert result.exit_code == 0
        assert "## Cluster Capacity" in result.output


# ── create-snapshot ──────────────────────────────────────────────


class TestCreateSnapshot:
    def test_basic_create(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "snap-new", "name": "my-snap"}
        result = runner.invoke(app, ["create-snapshot", "fs1", "my-snap", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "snap-new"
        mock_client.post.assert_called_once_with(
            "snapshots",
            json={"filesystem_uid": "fs1", "name": "my-snap", "is_writable": False},
        )

    def test_writable_with_access_point(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "snap-rw"}
        result = runner.invoke(
            app,
            [
                "create-snapshot",
                "fs1",
                "rw-snap",
                "--writable",
                "--access-point",
                "/mnt/snap",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["is_writable"] is True
        assert payload["access_point"] == "/mnt/snap"


# ── upload-snapshot ──────────────────────────────────────────────


class TestUploadSnapshot:
    def test_basic_upload(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"task_id": "upload-001"}
        result = runner.invoke(app, ["upload-snapshot", "snap1", "s3://backup", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["task_id"] == "upload-001"
        mock_client.post.assert_called_once_with(
            "snapshots/snap1/upload", json={"locator": "s3://backup"}
        )


# ── restore-fs ───────────────────────────────────────────────────


class TestRestoreFs:
    def test_basic_restore(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "fs-restored"}
        result = runner.invoke(app, ["restore-fs", "my-bucket", "snap-01", "new-fs", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["uid"] == "fs-restored"
        mock_client.post.assert_called_once_with(
            "fileSystems/download",
            json={
                "source_bucket": "my-bucket",
                "snapshot_name": "snap-01",
                "new_fs_name": "new-fs",
            },
        )


# ── manage-alert ─────────────────────────────────────────────────


class TestManageAlert:
    def test_mute_with_duration(self, mock_client: MagicMock) -> None:
        mock_client.put.return_value = {"status": "muted"}
        result = runner.invoke(
            app,
            [
                "manage-alert",
                "mute",
                "NodeDown",
                "--duration",
                "3600",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "muted"
        mock_client.put.assert_called_once_with("alerts/NodeDown/mute", json={"expiry": 3600})

    def test_unmute(self, mock_client: MagicMock) -> None:
        mock_client.put.return_value = {"status": "unmuted"}
        result = runner.invoke(app, ["manage-alert", "unmute", "NodeDown", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "unmuted"
        mock_client.put.assert_called_once_with("alerts/NodeDown/unmute")

    def test_mute_without_duration_exits_1(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["manage-alert", "mute", "NodeDown"])
        assert result.exit_code == 1

    def test_invalid_action_exits_1(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["manage-alert", "snooze", "NodeDown"])
        assert result.exit_code == 1


# ── delete-fs ────────────────────────────────────────────────────


class TestDeleteFs:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.delete.return_value = {"status": "deleted"}
        result = runner.invoke(app, ["delete-fs", "fs1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "deleted"
        mock_client.delete.assert_called_once_with("fileSystems/fs1")


# ── delete-org ───────────────────────────────────────────────────


class TestDeleteOrg:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.delete.return_value = {"status": "deleted"}
        result = runner.invoke(app, ["delete-org", "org1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "deleted"
        mock_client.delete.assert_called_once_with("organizations/org1")


# ── s3 buckets ───────────────────────────────────────────────────


class TestS3Buckets:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"name": "bucket1", "uid": "b1"}]}
        result = runner.invoke(app, ["s3", "buckets", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["name"] == "bucket1"
        mock_client.get.assert_called_once_with("s3/buckets")

    def test_human_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"uid": "b1", "name": "bucket1"}]}
        result = runner.invoke(app, ["s3", "buckets"])
        assert result.exit_code == 0
        assert "1 result(s)" in result.output


# ── s3 status ────────────────────────────────────────────────────


class TestS3Status:
    def test_json_output(self, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"data": [{"status": "running", "port": 9000}]}
        result = runner.invoke(app, ["s3", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["status"] == "running"
        mock_client.get.assert_called_once_with("s3")


# ── create-org ───────────────────────────────────────────────────


class TestCreateOrg:
    def test_basic_create(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "org-new", "name": "research"}
        result = runner.invoke(
            app,
            [
                "create-org",
                "research",
                "--ssd-quota",
                "500",
                "--total-quota",
                "2000",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["name"] == "research"
        assert payload["ssd_quota"] == 500 * 1024**3
        assert payload["total_quota"] == 2000 * 1024**3
        assert payload["username"] == "research"


# ── create-user ──────────────────────────────────────────────────


class TestCreateUser:
    def test_basic_create(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "u-new", "username": "alice"}
        result = runner.invoke(
            app,
            [
                "create-user",
                "alice",
                "--password",
                "secret123",
                "--json",
            ],
        )
        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "users",
            json={"username": "alice", "password": "secret123", "role": "OrgAdmin"},
        )

    def test_custom_role(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "u-new"}
        result = runner.invoke(
            app,
            [
                "create-user",
                "bob",
                "--password",
                "pass",
                "--role",
                "Regular",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["role"] == "Regular"


# ── create-fs-group ──────────────────────────────────────────────


class TestCreateFsGroup:
    def test_basic_create(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "fsg-1", "name": "ml-data"}
        result = runner.invoke(app, ["create-fs-group", "ml-data", "--json"])
        assert result.exit_code == 0
        mock_client.post.assert_called_once_with("fileSystemGroups", json={"name": "ml-data"})


# ── create-fs ────────────────────────────────────────────────────


class TestCreateFs:
    def test_basic_create(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "fs-new", "name": "myfs"}
        result = runner.invoke(app, ["create-fs", "myfs", "10TB", "--json"])
        assert result.exit_code == 0
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["name"] == "myfs"
        assert payload["total_capacity"] == 10 * 1024**4
        assert payload["auth_required"] is True

    def test_with_group(self, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"uid": "fs-new"}
        result = runner.invoke(app, ["create-fs", "myfs", "5TB", "--group", "ml", "--json"])
        assert result.exit_code == 0
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["group_name"] == "ml"


# ── update-org-quota ─────────────────────────────────────────────


class TestUpdateOrgQuota:
    def test_update_both_quotas(self, mock_client: MagicMock) -> None:
        mock_client.put.return_value = {"status": "ok"}
        result = runner.invoke(
            app,
            [
                "update-org-quota",
                "org1",
                "--ssd-quota",
                "520TB",
                "--total-quota",
                "1PB",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = mock_client.put.call_args.kwargs["json"]
        assert payload["ssd_quota"] == 520 * 1024**4
        assert payload["total_quota"] == 1 * 1024**5

    def test_no_quotas_exits_1(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["update-org-quota", "org1"])
        assert result.exit_code == 1


# ── update-fs ────────────────────────────────────────────────────


class TestUpdateFs:
    def test_update_capacity(self, mock_client: MagicMock) -> None:
        mock_client.put.return_value = {"uid": "fs1"}
        result = runner.invoke(
            app,
            [
                "update-fs",
                "fs1",
                "--total-capacity",
                "500TB",
                "--json",
            ],
        )
        assert result.exit_code == 0
        mock_client.put.assert_called_once_with("fileSystems/fs1", json={"total_capacity": "500TB"})

    def test_rename(self, mock_client: MagicMock) -> None:
        mock_client.put.return_value = {"uid": "fs1"}
        result = runner.invoke(
            app,
            [
                "update-fs",
                "fs1",
                "--new-name",
                "renamed-fs",
                "--json",
            ],
        )
        assert result.exit_code == 0
        mock_client.put.assert_called_once_with("fileSystems/fs1", json={"new_name": "renamed-fs"})

    def test_no_options_exits_1(self, mock_client: MagicMock) -> None:
        result = runner.invoke(app, ["update-fs", "fs1"])
        assert result.exit_code == 1


# ── env var validation ───────────────────────────────────────────


class TestClientEnvVars:
    def test_missing_env_vars_exits_1(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch("weka_mcp.cli._client") as mock_fn:
                from weka_mcp.cli import _client as real_client

                mock_fn.side_effect = real_client
                result = runner.invoke(app, ["filesystems"])
                assert result.exit_code == 1
