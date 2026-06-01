"""Tests for _validate_health helper and validate-health CLI command."""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maas_mcp.cli import _build_ssh_hints, _validate_health, app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _svc(name: str, status: str = "running", status_info: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "status_info": status_info}


def _make_rack(
    hostname: str = "rack1",
    system_id: str = "r1",
    version: str = "3.4.0",
    ip_addresses: list[str] | None = None,
    services: list[dict] | None = None,
) -> dict[str, Any]:
    if services is None:
        services = [
            _svc("rackd"),
            _svc("http"),
            _svc("tftp"),
            _svc("dhcpd", "off"),
            _svc("dhcpd6", "off"),
            _svc("ntp_rack", "unknown", "managed by the region"),
            _svc("proxy_rack", "unknown", "managed by the region"),
            _svc("syslog_rack", "off"),
            _svc("dns_rack", "off"),
        ]
    return {
        "hostname": hostname,
        "system_id": system_id,
        "node_type_name": "Rack controller",
        "version": version,
        "zone": {"name": "default"},
        "ip_addresses": ip_addresses if ip_addresses is not None else ["10.0.0.1"],
        "service_set": services,
    }


def _make_region(
    hostname: str = "region1",
    system_id: str = "rg1",
    version: str = "3.4.0",
    ip_addresses: list[str] | None = None,
    services: list[dict] | None = None,
) -> dict[str, Any]:
    if services is None:
        services = [
            _svc("regiond"),
            _svc("proxy"),
            _svc("reverse_proxy"),
            _svc("temporal"),
            _svc("temporal-worker"),
        ]
    return {
        "hostname": hostname,
        "system_id": system_id,
        "node_type_name": "Region controller",
        "version": version,
        "zone": {"name": "default"},
        "ip_addresses": ip_addresses if ip_addresses is not None else ["10.0.0.10"],
        "service_set": services,
    }


def _make_region_rack(
    hostname: str = "region-rack1",
    system_id: str = "rr1",
    version: str = "3.4.0",
    ip_addresses: list[str] | None = None,
    services: list[dict] | None = None,
) -> dict[str, Any]:
    """Region+rack controller that appears in both rackcontrollers and regioncontrollers."""
    if services is None:
        services = [
            _svc("rackd"),
            _svc("http"),
            _svc("tftp"),
            _svc("regiond"),
            _svc("proxy"),
            _svc("reverse_proxy"),
            _svc("temporal"),
            _svc("temporal-worker"),
            _svc("dhcpd", "off"),
            _svc("dhcpd6", "off"),
        ]
    return {
        "hostname": hostname,
        "system_id": system_id,
        "node_type_name": "Region and rack controller",
        "version": version,
        "zone": {"name": "default"},
        "ip_addresses": ip_addresses if ip_addresses is not None else ["10.0.0.50"],
        "service_set": services,
    }


def _boot_resource(rid: int = 1, name: str = "ubuntu/jammy", complete: bool = True) -> dict:
    return {
        "id": rid,
        "name": name,
        "architecture": "amd64/generic",
        "type": "synced",
        "complete": complete,
    }


def _make_client(
    racks: list[dict] | None = None,
    regions: list[dict] | None = None,
    resources: list[dict] | None = None,
    is_importing: bool = False,
    http_proxy: str = "",
    fabrics: list[dict] | None = None,
    vlans_by_fabric: dict[int, list[dict]] | None = None,
) -> MagicMock:
    if racks is None:
        racks = [_make_rack()]
    if regions is None:
        regions = [_make_region()]
    if resources is None:
        resources = [_boot_resource()]
    if fabrics is None:
        fabrics = []
    if vlans_by_fabric is None:
        vlans_by_fabric = {}

    client = MagicMock()

    def _get(endpoint: str, params: dict | None = None):
        if endpoint == "rackcontrollers":
            return racks
        if endpoint == "regioncontrollers":
            return regions
        if endpoint == "boot-resources":
            if params and params.get("op") == "is_importing":
                return is_importing
            return resources
        if endpoint == "maas":
            if params and params.get("name") == "http_proxy":
                return http_proxy
            return ""
        if endpoint == "fabrics":
            return fabrics
        if endpoint.startswith("fabrics/") and endpoint.endswith("/vlans"):
            fid = int(endpoint.split("/")[1])
            return vlans_by_fabric.get(fid, [])
        return []

    client.get.side_effect = _get
    return client


# ---------------------------------------------------------------------------
# _validate_health — all healthy
# ---------------------------------------------------------------------------


class TestAllHealthy:
    def test_all_healthy(self):
        client = _make_client()
        result = _validate_health(client)

        assert result["ok"] is True
        assert result["issues"] == []
        assert result["ssh_commands"] == []
        assert result["controllers"]["ok"] is True
        assert result["proxy"]["ok"] is True
        assert result["images"]["ok"] is True
        assert result["versions"]["ok"] is True
        assert result["networking"]["ok"] is True


# ---------------------------------------------------------------------------
# Controller service failures
# ---------------------------------------------------------------------------


class TestControllerServices:
    def test_rack_http_dead(self):
        rack = _make_rack(services=[
            _svc("rackd"),
            _svc("http", "dead"),
            _svc("tftp"),
        ])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        assert result["ok"] is False
        ctrl = result["controllers"]["rack_controllers"][0]
        assert ctrl["status"] == "degraded"
        assert any("http" in i for i in ctrl["issues"])
        assert len(result["ssh_commands"]) > 0

    def test_rack_all_dead(self):
        rack = _make_rack(services=[
            _svc("rackd", "dead"),
            _svc("http", "dead"),
            _svc("tftp", "dead"),
        ])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        assert result["ok"] is False
        ctrl = result["controllers"]["rack_controllers"][0]
        assert ctrl["status"] == "offline"

    def test_region_proxy_dead(self):
        region = _make_region(services=[
            _svc("regiond"),
            _svc("proxy", "dead"),
            _svc("reverse_proxy"),
            _svc("temporal"),
            _svc("temporal-worker"),
        ])
        client = _make_client(regions=[region])
        result = _validate_health(client)

        assert result["ok"] is False
        assert result["proxy"]["ok"] is False
        assert any("proxy" in i for i in result["proxy"]["issues"])

    def test_exempt_services_not_flagged(self):
        rack = _make_rack(services=[
            _svc("rackd"),
            _svc("http"),
            _svc("tftp"),
            _svc("dhcpd", "off"),
            _svc("dhcpd6", "off"),
            _svc("ntp_rack", "unknown", "managed by the region"),
            _svc("proxy_rack", "unknown", "managed by the region"),
            _svc("syslog_rack", "off"),
            _svc("dns_rack", "off"),
        ])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        assert result["ok"] is True
        ctrl = result["controllers"]["rack_controllers"][0]
        assert ctrl["status"] == "healthy"
        assert ctrl["issues"] == []


# ---------------------------------------------------------------------------
# Version consistency
# ---------------------------------------------------------------------------


class TestVersionSkew:
    def test_version_skew(self):
        rack1 = _make_rack(hostname="rack1", system_id="r1", version="3.4.0")
        rack2 = _make_rack(hostname="rack2", system_id="r2", version="3.3.0",
                           ip_addresses=["10.0.0.2"])
        client = _make_client(racks=[rack1, rack2])
        result = _validate_health(client)

        assert result["ok"] is False
        assert result["versions"]["ok"] is False
        assert result["versions"]["expected"] == "3.4.0"
        assert any("rack2" in i for i in result["versions"]["issues"])


# ---------------------------------------------------------------------------
# Boot images
# ---------------------------------------------------------------------------


class TestBootImages:
    def test_importing(self):
        client = _make_client(is_importing=True)
        result = _validate_health(client)

        assert result["ok"] is False
        assert result["images"]["ok"] is False
        assert result["images"]["is_importing"] is True

    def test_no_resources(self):
        client = _make_client(resources=[])
        result = _validate_health(client)

        assert result["ok"] is False
        assert result["images"]["ok"] is False
        assert any("No synced" in i for i in result["images"]["issues"])

    def test_incomplete_resource(self):
        res = _boot_resource(complete=False)
        client = _make_client(resources=[res])
        result = _validate_health(client)

        assert result["ok"] is False
        assert result["images"]["incomplete_count"] == 1


# ---------------------------------------------------------------------------
# Database checks
# ---------------------------------------------------------------------------


class TestDatabase:
    def test_db_skipped(self):
        client = _make_client()
        result = _validate_health(client, db_url=None)

        assert result["database"]["skipped"] is True
        assert result["database"]["ok"] is True

    def test_db_connected(self):
        client = _make_client()

        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [(42,), (0,)]
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = mock_conn
        saved = sys.modules.pop("psycopg", None)
        try:
            with patch.dict(
                "sys.modules", {"psycopg": mock_psycopg}
            ):
                result = _validate_health(
                    client,
                    db_url="postgresql://localhost/maas",
                )
        finally:
            if saved is not None:
                sys.modules["psycopg"] = saved

        assert result["database"]["ok"] is True
        assert result["database"]["connected"] is True
        assert result["database"]["node_count"] == 42


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------


class TestNetworking:
    def test_dhcp_vlan_offline_rack(self):
        rack = _make_rack(services=[
            _svc("rackd", "dead"),
            _svc("http", "dead"),
            _svc("tftp", "dead"),
        ])
        fabrics = [{"id": 1}]
        vlans = {1: [{"vid": 100, "dhcp_on": True, "primary_rack": "r1", "secondary_rack": None}]}
        client = _make_client(racks=[rack], fabrics=fabrics, vlans_by_fabric=vlans)
        result = _validate_health(client)

        assert result["networking"]["ok"] is False
        assert any("offline" in i for i in result["networking"]["dhcp_vlan_issues"])

    def test_duplicate_ips(self):
        rack1 = _make_rack(hostname="rack1", system_id="r1", ip_addresses=["10.0.0.1"])
        rack2 = _make_rack(hostname="rack2", system_id="r2", ip_addresses=["10.0.0.1"])
        client = _make_client(racks=[rack1, rack2])
        result = _validate_health(client)

        assert result["networking"]["ok"] is False
        assert len(result["networking"]["duplicate_ips"]) == 1

    def test_no_ips(self):
        rack = _make_rack(ip_addresses=[])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        assert result["networking"]["ok"] is False
        assert "rack1" in result["networking"]["controllers_with_no_ips"]


# ---------------------------------------------------------------------------
# SSH hints
# ---------------------------------------------------------------------------


class TestSshHints:
    def test_ssh_hints_use_correct_ip(self):
        rack = _make_rack(ip_addresses=["192.168.1.5", "10.0.0.1"])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        ctrl = result["controllers"]["rack_controllers"][0]
        for cmd in ctrl["ssh_commands"].values():
            assert "192.168.1.5" in cmd

    def test_ssh_hints_region_includes_regiond(self):
        hints = _build_ssh_hints("region1", "10.0.0.10", "region")
        assert "check_regiond_logs" in hints
        assert "regiond" in hints["check_regiond_logs"]

    def test_ssh_hints_rack_no_regiond(self):
        hints = _build_ssh_hints("rack1", "10.0.0.1", "rack")
        assert "check_regiond_logs" not in hints

    def test_no_ssh_hints_without_ip(self):
        rack = _make_rack(ip_addresses=[])
        client = _make_client(racks=[rack])
        result = _validate_health(client)

        ctrl = result["controllers"]["rack_controllers"][0]
        assert ctrl["ssh_commands"] == {}


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_json_output(self, runner: CliRunner):
        client = _make_client()
        with patch("maas_mcp.cli._get_client", return_value=("default", client)):
            result = runner.invoke(app, ["validate-health", "--json", "--skip-db"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "controllers" in data

    def test_cli_exit_code_unhealthy(self, runner: CliRunner):
        rack = _make_rack(services=[
            _svc("rackd"),
            _svc("http", "dead"),
            _svc("tftp"),
        ])
        client = _make_client(racks=[rack])
        with patch("maas_mcp.cli._get_client", return_value=("default", client)):
            result = runner.invoke(app, ["validate-health", "--json", "--skip-db"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False

    def test_cli_text_output_healthy(self, runner: CliRunner):
        client = _make_client()
        with patch("maas_mcp.cli._get_client", return_value=("default", client)):
            result = runner.invoke(app, ["validate-health", "--skip-db"])

        assert result.exit_code == 0
        assert "PASS" in result.stdout

    def test_cli_text_output_unhealthy(self, runner: CliRunner):
        rack = _make_rack(services=[
            _svc("rackd"),
            _svc("http", "dead"),
            _svc("tftp"),
        ])
        client = _make_client(racks=[rack])
        with patch("maas_mcp.cli._get_client", return_value=("default", client)):
            result = runner.invoke(app, ["validate-health", "--skip-db"])

        assert result.exit_code == 1
        assert "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# Region+rack controller deduplication
# ---------------------------------------------------------------------------


class TestRegionRackDedup:
    """Regression tests for region+rack controllers that appear in both API lists."""

    def test_no_duplicate_version_entries(self):
        """Bug 1: region+rack controller must not produce duplicate version entries."""
        rr = _make_region_rack(hostname="ip-10-10-3-120", system_id="rr1", version="3.6.3")
        rack1 = _make_rack(hostname="rack1", system_id="r1", version="3.6.4",
                           ip_addresses=["10.0.0.1"])
        rack2 = _make_rack(hostname="rack2", system_id="r2", version="3.6.4",
                           ip_addresses=["10.0.0.2"])
        client = _make_client(racks=[rr, rack1, rack2], regions=[rr])
        result = _validate_health(client)

        hostnames_in_versions = [c["hostname"] for c in result["versions"]["controllers"]]
        assert hostnames_in_versions.count("ip-10-10-3-120") == 1
        assert result["versions"]["expected"] == "3.6.4"
        assert len(result["versions"]["issues"]) == 1
        assert "ip-10-10-3-120" in result["versions"]["issues"][0]

    def test_region_rack_excluded_from_region_results(self):
        """Bug 2: region+rack controller must not appear in region_controllers output."""
        rr = _make_region_rack(hostname="ip-10-10-3-120", system_id="rr1")
        client = _make_client(racks=[rr], regions=[rr])
        result = _validate_health(client)

        assert len(result["controllers"]["region_controllers"]) == 0
        rack_ctrl = result["controllers"]["rack_controllers"][0]
        assert rack_ctrl["hostname"] == "ip-10-10-3-120"
        assert len(rack_ctrl["services"]) > 0

    def test_pure_region_retains_services(self):
        """Pure region controllers must still have services extracted."""
        rack = _make_rack()
        region = _make_region()
        client = _make_client(racks=[rack], regions=[region])
        result = _validate_health(client)

        region_ctrl = result["controllers"]["region_controllers"][0]
        assert region_ctrl["hostname"] == "region1"
        assert "regiond" in region_ctrl["services"]
        assert region_ctrl["services"]["regiond"] == "running"

    def test_mixed_topology_healthy(self):
        """Topology with rack, pure-region, and region+rack is healthy with no dupes."""
        rack = _make_rack(hostname="rack1", system_id="r1", ip_addresses=["10.0.0.1"])
        region = _make_region(hostname="region1", system_id="rg1", ip_addresses=["10.0.0.10"])
        rr = _make_region_rack(hostname="rr1", system_id="rr1", ip_addresses=["10.0.0.50"])
        client = _make_client(racks=[rack, rr], regions=[region, rr])
        result = _validate_health(client)

        assert result["ok"] is True
        assert len(result["controllers"]["rack_controllers"]) == 2
        assert len(result["controllers"]["region_controllers"]) == 1
        version_hostnames = [c["hostname"] for c in result["versions"]["controllers"]]
        assert sorted(version_hostnames) == ["rack1", "region1", "rr1"]
