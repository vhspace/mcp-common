"""Tests for the netbox_update_device MCP tool."""

from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from netbox_mcp.server import netbox_update_device

MOCK_DEVICE = {
    "id": 42,
    "name": "gpu-node-01.dc1.together.ai",
    "status": {"value": "active", "label": "Active"},
    "site": {"id": 1, "name": "DC1", "slug": "dc1"},
    "cluster": {"id": 5, "name": "cartesia5"},
    "rack": {"id": 10, "name": "R01"},
    "device_role": {"id": 2, "name": "GPU Node"},
    "device_type": {"id": 5, "name": "DGX H100"},
}

UPDATED_DEVICE = {
    **MOCK_DEVICE,
    "status": {"value": "offline", "label": "Offline"},
}


class TestUpdateDeviceValidation:
    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_requires_at_least_one_field(self, mock_netbox):
        with pytest.raises(ToolError, match="At least one"):
            netbox_update_device(device="gpu-node-01")

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_rejects_invalid_status(self, mock_netbox):
        with pytest.raises(ToolError, match="Invalid status"):
            netbox_update_device(device="gpu-node-01", status="bogus")

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_rejects_no_match(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 0, "results": []}
        with pytest.raises(ToolError, match="No device found"):
            netbox_update_device(device="nonexistent", status="active")

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_rejects_ambiguous_match(self, mock_netbox):
        d1 = {**MOCK_DEVICE, "name": "gpu-node-01"}
        d2 = {**MOCK_DEVICE, "id": 43, "name": "gpu-node-02"}
        mock_netbox.get.return_value = {"count": 2, "results": [d1, d2]}
        with pytest.raises(ToolError, match="Multiple devices"):
            netbox_update_device(device="gpu-node", status="active")


class TestUpdateDeviceStatus:
    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_updates_status_by_hostname(self, mock_netbox):
        mock_netbox.get.return_value = {
            "count": 1,
            "results": [MOCK_DEVICE.copy()],
        }
        mock_netbox.patch.return_value = UPDATED_DEVICE.copy()

        result = netbox_update_device(device="gpu-node-01", status="offline")

        assert result["device"]["status"]["value"] == "offline"
        assert "status: active → offline" in result["changes"]
        mock_netbox.patch.assert_called_once_with(
            "dcim/devices", id=42, data={"status": "offline"}
        )

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_updates_status_by_numeric_id(self, mock_netbox):
        mock_netbox.get.return_value = MOCK_DEVICE.copy()
        mock_netbox.patch.return_value = UPDATED_DEVICE.copy()

        result = netbox_update_device(device="42", status="offline")

        assert result["device"]["status"]["value"] == "offline"
        mock_netbox.get.assert_called_once_with("dcim/devices", id=42)

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_updates_cluster(self, mock_netbox):
        cluster_resp = {"count": 1, "results": [{"id": 10, "name": "newcluster"}]}
        device_resp = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        updated = {**MOCK_DEVICE, "cluster": {"id": 10, "name": "newcluster"}}

        mock_netbox.get.side_effect = [device_resp, cluster_resp]
        mock_netbox.patch.return_value = updated

        result = netbox_update_device(device="gpu-node-01", cluster="newcluster")

        assert "cluster: cartesia5 → newcluster" in result["changes"]
        mock_netbox.patch.assert_called_once_with(
            "dcim/devices", id=42, data={"cluster": 10}
        )

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_updates_status_and_cluster(self, mock_netbox):
        cluster_resp = {"count": 1, "results": [{"id": 10, "name": "newcluster"}]}
        device_resp = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        updated = {
            **MOCK_DEVICE,
            "status": {"value": "planned", "label": "Planned"},
            "cluster": {"id": 10, "name": "newcluster"},
        }

        mock_netbox.get.side_effect = [device_resp, cluster_resp]
        mock_netbox.patch.return_value = updated

        result = netbox_update_device(
            device="gpu-node-01", status="planned", cluster="newcluster"
        )

        assert len(result["changes"]) == 2
        mock_netbox.patch.assert_called_once_with(
            "dcim/devices",
            id=42,
            data={"status": "planned", "cluster": 10},
        )

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_rejects_unknown_cluster(self, mock_netbox):
        device_resp = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        cluster_resp = {"count": 0, "results": []}

        mock_netbox.get.side_effect = [device_resp, cluster_resp]

        with pytest.raises(ToolError, match="Cluster.*not found"):
            netbox_update_device(device="gpu-node-01", cluster="nope")


class TestUpdateDeviceVPN:
    @patch("netbox_mcp.server.netbox")
    def test_checks_vpn_when_monitor_present(self, mock_netbox):
        monitor = MagicMock()
        monitor.require_vpn.side_effect = ValueError("VPN not connected")

        with patch("netbox_mcp.server.vpn_monitor", monitor):
            with pytest.raises(ToolError, match="VPN not connected"):
                netbox_update_device(device="gpu-node-01", status="active")

    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_skips_vpn_check_when_no_monitor(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        mock_netbox.patch.return_value = UPDATED_DEVICE.copy()

        result = netbox_update_device(device="gpu-node-01", status="offline")
        assert result["device"] is not None


class TestUpdateDeviceAllStatuses:
    """Verify every valid status value is accepted."""

    @pytest.mark.parametrize(
        "status",
        ["active", "planned", "staged", "failed", "inventory", "decommissioning", "offline"],
    )
    @patch("netbox_mcp.server.vpn_monitor", None)
    @patch("netbox_mcp.server.netbox")
    def test_valid_status_accepted(self, mock_netbox, status):
        mock_netbox.get.return_value = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        mock_netbox.patch.return_value = {**MOCK_DEVICE, "status": {"value": status}}

        result = netbox_update_device(device="gpu-node-01", status=status)
        assert result["device"]["status"]["value"] == status
