"""Tests for Dell GRUB recovery module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from redfish_mcp.dell_recovery import (
    RecoveryResult,
    _check_prerequisites,
    _ensure_disk_boot,
    _ensure_serial_console,
    run_dell_grub_recovery,
)
from redfish_mcp.mcp_server import create_mcp_app


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


class TestRecoveryResult:
    def test_log_and_to_dict(self):
        r = RecoveryResult(ok=True, host="10.0.0.1")
        r.log("step1", "OK", "detail")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["host"] == "10.0.0.1"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["step"] == "step1"

    def test_to_dict_with_health(self):
        r = RecoveryResult(
            ok=False,
            host="10.0.0.1",
            health_before="Critical",
            health_after="OK",
            error="something",
        )
        d = r.to_dict()
        assert d["health_before"] == "Critical"
        assert d["health_after"] == "OK"
        assert d["error"] == "something"


class TestPrerequisites:
    @patch("shutil.which", return_value=None)
    def test_missing_sshpass(self, _mock_which):
        err = _check_prerequisites()
        assert err is not None
        assert "sshpass" in err

    @patch("shutil.which", return_value="/usr/bin/sshpass")
    def test_all_present(self, _mock_which):
        err = _check_prerequisites()
        assert err is None


class TestEnsureSerialConsole:
    @patch("redfish_mcp.dell_recovery._racadm_ssh")
    def test_already_configured(self, mock_ssh):
        mock_ssh.return_value = (0, "SerialComm=OnConRedir\nSerialPortAddress=Com2\n")
        needs_reboot, msg = _ensure_serial_console("host", "user", "pass")
        assert needs_reboot is False
        assert "already configured" in msg

    @patch("redfish_mcp.dell_recovery._racadm_ssh")
    def test_needs_configuration(self, mock_ssh):
        mock_ssh.side_effect = [
            (0, "SerialComm=Off\nSerialPortAddress=Com1\n"),  # get
            (0, "RAC1017: Successfully modified"),  # set SerialComm
            (0, "RAC1017: Successfully modified"),  # set SerialPortAddress
            (0, "RAC1024: Successfully scheduled"),  # jobqueue create
        ]
        needs_reboot, msg = _ensure_serial_console("host", "user", "pass")
        assert needs_reboot is True
        assert "configured" in msg


class TestEnsureDiskBoot:
    @patch("redfish_mcp.dell_recovery._racadm_ssh")
    def test_pxe_already_disabled(self, mock_ssh):
        mock_ssh.return_value = (0, "PxeDev1EnDis=Disabled\n")
        needs_reboot, _msg = _ensure_disk_boot("host", "user", "pass")
        assert needs_reboot is False

    @patch("redfish_mcp.dell_recovery._racadm_ssh")
    def test_pxe_needs_disable(self, mock_ssh):
        mock_ssh.side_effect = [
            (0, "PxeDev1EnDis=Enabled\n"),  # get
            (0, "RAC1017: Successfully modified"),  # set
            (0, "RAC1024: Successfully scheduled"),  # jobqueue
        ]
        needs_reboot, msg = _ensure_disk_boot("host", "user", "pass")
        assert needs_reboot is True
        assert "PXE disabled" in msg


class TestRunRecovery:
    @patch("redfish_mcp.dell_recovery._check_prerequisites", return_value="sshpass missing")
    def test_missing_prerequisites(self, _mock):
        result = run_dell_grub_recovery(
            host="10.0.0.1",
            user="admin",
            password="pass",
            service_name="bad.service",
            kernel_version="6.8.0",
            root_uuid="abc-123",
        )
        assert result.ok is False
        assert "sshpass" in (result.error or "")

    @patch("redfish_mcp.dell_recovery._check_prerequisites", return_value=None)
    @patch("redfish_mcp.dell_recovery._redfish_get_health")
    @patch("redfish_mcp.dell_recovery._ensure_serial_console")
    @patch("redfish_mcp.dell_recovery._ensure_disk_boot")
    @patch("redfish_mcp.dell_recovery._sol_grub_boot")
    @patch("time.sleep")
    def test_full_success(
        self, mock_sleep, mock_sol, mock_disk, mock_serial, mock_health, mock_prereq
    ):
        mock_health.side_effect = ["Critical", "OK"]
        mock_serial.return_value = (False, "already configured")
        mock_disk.return_value = (False, "PXE already disabled")
        mock_sol.return_value = (True, "GRUB boot command sent")

        result = run_dell_grub_recovery(
            host="10.0.0.1",
            user="admin",
            password="pass",
            service_name="disable_acs.service",
            kernel_version="6.8.0-101-generic",
            root_uuid="ad957c5a-24ed-4797-8740-4cd3c5cbf19c",
            re_enable_pxe=False,
        )

        assert result.ok is True
        assert result.health_before == "Critical"
        assert result.health_after == "OK"

    @patch("redfish_mcp.dell_recovery._check_prerequisites", return_value=None)
    @patch("redfish_mcp.dell_recovery._redfish_get_health")
    @patch("redfish_mcp.dell_recovery._ensure_serial_console")
    @patch("redfish_mcp.dell_recovery._ensure_disk_boot")
    @patch("redfish_mcp.dell_recovery._sol_grub_boot")
    @patch("time.sleep")
    def test_sol_failure(
        self, mock_sleep, mock_sol, mock_disk, mock_serial, mock_health, mock_prereq
    ):
        mock_health.return_value = "Critical"
        mock_serial.return_value = (False, "ok")
        mock_disk.return_value = (False, "ok")
        mock_sol.return_value = (False, "GRUB not detected")

        result = run_dell_grub_recovery(
            host="10.0.0.1",
            user="admin",
            password="pass",
            service_name="bad.service",
            kernel_version="6.8.0",
            root_uuid="abc-123",
            re_enable_pxe=False,
        )

        assert result.ok is False
        assert "GRUB" in (result.error or "")


class TestMcpToolRegistration:
    def test_tool_registered(self, mcp_tools):
        assert "redfish_dell_grub_recovery" in mcp_tools

    @pytest.mark.anyio
    async def test_requires_allow_write(self, mcp_tools):
        result = await mcp_tools["redfish_dell_grub_recovery"](
            host="10.0.0.1",
            user="admin",
            password="pass",
            service_name="bad.service",
            kernel_version="6.8.0",
            root_uuid="abc-123",
        )
        assert result["ok"] is False
        assert "allow_write" in result["error"]
