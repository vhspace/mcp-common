"""Tests for bond_audit helpers: SSH parsing, MAAS extraction, comparison logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from maas_mcp.bond_audit import (
    _parse_ssh_output,
    async_ssh_bond_info,
    build_audit_result,
    build_summary,
    extract_maas_bond_config,
    resolve_cluster_hostnames,
    resolve_maas_hostnames,
    ssh_bond_info,
)


class TestParseSSHOutput:
    def test_normal_output(self):
        raw = "enp1s0f0np0\n|||\nenp1s0f0np0 enp1s0f1np1\n"
        result = _parse_ssh_output(raw)
        assert result["active_slave"] == "enp1s0f0np0"
        assert result["slaves"] == ["enp1s0f0np0", "enp1s0f1np1"]

    def test_empty_output(self):
        result = _parse_ssh_output("")
        assert result["active_slave"] is None
        assert result["slaves"] == []

    def test_only_separator(self):
        result = _parse_ssh_output("|||")
        assert result["active_slave"] is None
        assert result["slaves"] == []

    def test_active_only_no_slaves(self):
        raw = "eth0\n|||\n"
        result = _parse_ssh_output(raw)
        assert result["active_slave"] == "eth0"
        assert result["slaves"] == []

    def test_whitespace_handling(self):
        raw = "  eth0  \n|||  \n  eth0   eth1  \n"
        result = _parse_ssh_output(raw)
        assert result["active_slave"] == "eth0"
        assert result["slaves"] == ["eth0", "eth1"]


class TestExtractMaaSBondConfig:
    def test_bond_found_with_explicit_primary(self):
        machine = {
            "interface_set": [
                {"type": "physical", "name": "enp1s0f0np0", "id": 1},
                {"type": "physical", "name": "enp1s0f1np1", "id": 2},
                {
                    "type": "bond",
                    "name": "bond0",
                    "parents": ["enp1s0f0np0", "enp1s0f1np1"],
                    "params": {"bond_primary": "enp1s0f1np1"},
                },
            ]
        }
        result = extract_maas_bond_config(machine)
        assert result is not None
        assert result["parents"] == ["enp1s0f0np0", "enp1s0f1np1"]
        assert result["bond_primary"] == "enp1s0f1np1"
        assert result["effective_primary"] == "enp1s0f1np1"

    def test_bond_found_no_explicit_primary(self):
        machine = {
            "interface_set": [
                {"type": "physical", "name": "enp1s0f0np0", "id": 1},
                {"type": "physical", "name": "enp1s0f1np1", "id": 2},
                {
                    "type": "bond",
                    "name": "bond0",
                    "parents": ["enp1s0f0np0", "enp1s0f1np1"],
                    "params": {},
                },
            ]
        }
        result = extract_maas_bond_config(machine)
        assert result is not None
        assert result["bond_primary"] is None
        assert result["effective_primary"] == "enp1s0f0np0"

    def test_no_bond_interface(self):
        machine = {
            "interface_set": [
                {"type": "physical", "name": "enp1s0f0np0", "id": 1},
            ]
        }
        assert extract_maas_bond_config(machine) is None

    def test_different_bond_name(self):
        machine = {
            "interface_set": [
                {
                    "type": "bond",
                    "name": "bond1",
                    "parents": ["eth0", "eth1"],
                    "params": {},
                },
            ]
        }
        assert extract_maas_bond_config(machine, "bond0") is None
        result = extract_maas_bond_config(machine, "bond1")
        assert result is not None
        assert result["parents"] == ["eth0", "eth1"]

    def test_parents_as_integer_ids(self):
        machine = {
            "interface_set": [
                {"type": "physical", "name": "eth0", "id": 10},
                {"type": "physical", "name": "eth1", "id": 20},
                {
                    "type": "bond",
                    "name": "bond0",
                    "parents": [10, 20],
                    "params": {},
                },
            ]
        }
        result = extract_maas_bond_config(machine)
        assert result is not None
        assert result["parents"] == ["eth0", "eth1"]

    def test_params_none(self):
        machine = {
            "interface_set": [
                {
                    "type": "bond",
                    "name": "bond0",
                    "parents": ["eth0"],
                    "params": None,
                },
            ]
        }
        result = extract_maas_bond_config(machine)
        assert result is not None
        assert result["bond_primary"] is None

    def test_primary_param_fallback(self):
        """The ``primary`` key (alias) is used when ``bond_primary`` is absent."""
        machine = {
            "interface_set": [
                {
                    "type": "bond",
                    "name": "bond0",
                    "parents": ["eth0", "eth1"],
                    "params": {"primary": "eth1"},
                },
            ]
        }
        result = extract_maas_bond_config(machine)
        assert result is not None
        assert result["bond_primary"] == "eth1"
        assert result["effective_primary"] == "eth1"


class TestBuildAuditResult:
    def test_matching(self):
        ssh = {"active_slave": "eth0", "slaves": ["eth0", "eth1"]}
        bond = {"parents": ["eth0", "eth1"], "bond_primary": None, "effective_primary": "eth0"}
        r = build_audit_result("gpu001", "abc123", ssh, bond)
        assert r["match"] is True
        assert r["error"] is None
        assert r["hostname"] == "gpu001"
        assert r["system_id"] == "abc123"

    def test_mismatch(self):
        ssh = {"active_slave": "eth1", "slaves": ["eth0", "eth1"]}
        bond = {"parents": ["eth0", "eth1"], "bond_primary": None, "effective_primary": "eth0"}
        r = build_audit_result("gpu001", "abc123", ssh, bond)
        assert r["match"] is False

    def test_ssh_error(self):
        ssh = {"active_slave": None, "slaves": [], "error": "ssh timeout"}
        bond = {"parents": ["eth0"], "bond_primary": None, "effective_primary": "eth0"}
        r = build_audit_result("gpu001", "abc123", ssh, bond)
        assert r["match"] is None
        assert r["error"] == "ssh timeout"

    def test_no_maas_bond(self):
        ssh = {"active_slave": "eth0", "slaves": ["eth0"]}
        r = build_audit_result("gpu001", "abc123", ssh, None)
        assert r["match"] is None
        assert r["error"] == "no bond config in MAAS"

    def test_both_ssh_error_and_no_bond(self):
        ssh = {"active_slave": None, "slaves": [], "error": "connection refused"}
        r = build_audit_result("gpu001", None, ssh, None)
        assert r["error"] == "connection refused"


class TestBuildSummary:
    def test_mixed_results(self):
        results = [
            {"match": True, "error": None},
            {"match": False, "error": None},
            {"match": None, "error": "timeout"},
            {"match": True, "error": None},
        ]
        s = build_summary(results)
        assert s == {"total": 4, "matches": 2, "mismatches": 1, "errors": 1}

    def test_empty(self):
        assert build_summary([]) == {"total": 0, "matches": 0, "mismatches": 0, "errors": 0}

    def test_all_match(self):
        results = [{"match": True, "error": None}] * 3
        s = build_summary(results)
        assert s["matches"] == 3
        assert s["mismatches"] == 0


class TestSSHBondInfo:
    @patch("maas_mcp.bond_audit.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="enp1s0f0np0\n|||\nenp1s0f0np0 enp1s0f1np1\n",
            stderr="",
        )
        result = ssh_bond_info("gpu001")
        assert result["active_slave"] == "enp1s0f0np0"
        assert result["slaves"] == ["enp1s0f0np0", "enp1s0f1np1"]
        assert "error" not in result

    @patch("maas_mcp.bond_audit.subprocess.run")
    def test_ssh_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=255,
            stdout="",
            stderr="Connection refused",
        )
        result = ssh_bond_info("gpu001")
        assert result["error"] == "ssh exit 255: Connection refused"

    @patch("maas_mcp.bond_audit.subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        result = ssh_bond_info("gpu001")
        assert result["error"] == "ssh timeout"


class TestAsyncSSHBondInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        from unittest.mock import AsyncMock

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"eth0\n|||\neth0 eth1\n", b""))

        with patch(
            "maas_mcp.bond_audit.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await async_ssh_bond_info("gpu001")
            assert result["active_slave"] == "eth0"
            assert result["slaves"] == ["eth0", "eth1"]


class TestResolveClusterHostnames:
    def test_resolves_devices(self):
        nb = MagicMock()
        nb._get.return_value = {
            "results": [
                {
                    "name": "research-h100-001",
                    "custom_fields": {"Provider_Machine_ID": "gpu001"},
                },
                {
                    "name": "research-h100-002",
                    "custom_fields": {"Provider_Machine_ID": "gpu002"},
                },
            ],
            "next": None,
        }
        result = resolve_cluster_hostnames(nb, "research-h100")
        assert len(result) == 2
        assert result[0] == {"netbox_name": "research-h100-001", "maas_hostname": "gpu001"}

    def test_skips_missing_provider_id(self):
        nb = MagicMock()
        nb._get.return_value = {
            "results": [
                {"name": "dev-001", "custom_fields": {}},
                {"name": "dev-002", "custom_fields": {"Provider_Machine_ID": "gpu002"}},
            ],
            "next": None,
        }
        result = resolve_cluster_hostnames(nb, "dev-cluster")
        assert len(result) == 1
        assert result[0]["maas_hostname"] == "gpu002"


class TestResolveMaaSHostnames:
    def test_resolves_existing_host(self):
        client = MagicMock()
        client.get.return_value = [
            {"system_id": "abc123", "hostname": "gpu001", "interface_set": []}
        ]
        result = resolve_maas_hostnames(client, ["gpu001"])
        assert result["gpu001"]["system_id"] == "abc123"

    def test_host_not_found(self):
        client = MagicMock()
        client.get.return_value = []
        result = resolve_maas_hostnames(client, ["gpu999"])
        assert result["gpu999"]["error"] == "not found in MAAS"

    def test_api_error(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError("API timeout")
        result = resolve_maas_hostnames(client, ["gpu001"])
        assert "API timeout" in result["gpu001"]["error"]
