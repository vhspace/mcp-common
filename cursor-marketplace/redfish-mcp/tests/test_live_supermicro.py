"""Live integration tests against a real Supermicro/Ori Cloud BMC.

Target: research-common-h100-074 at ORI-TX (192.168.196.74)

Run with:
    REDFISH_USER=admin REDFISH_PASSWORD=xxx uv run pytest tests/test_live_supermicro.py -v

These tests are READ-ONLY and safe to run against production BMCs.
Skipped automatically when credentials are unavailable or BMC is unreachable.
"""

from __future__ import annotations

import os
import unittest

import pytest

# Default test target — override with REDFISH_TEST_HOST env var
DEFAULT_HOST = "192.168.196.74"

_user = os.environ.get("REDFISH_USER", "")
_password = os.environ.get("REDFISH_PASSWORD", "")
_host = os.environ.get("REDFISH_TEST_HOST", DEFAULT_HOST)

_skip_reason = ""
if not _user or not _password:
    _skip_reason = "REDFISH_USER and REDFISH_PASSWORD env vars required"


def _check_reachable():
    """Quick connectivity check — skip if BMC is unreachable."""
    if _skip_reason:
        return _skip_reason
    try:
        from redfish_mcp.redfish import RedfishClient

        c = RedfishClient(
            host=_host, user=_user, password=_password, verify_tls=False, timeout_s=10
        )
        c.discover_system()
        c.close()
        return ""
    except Exception as e:
        return f"BMC unreachable at {_host}: {e}"


_reachable_skip = _check_reachable()
skip_live = pytest.mark.skipif(
    bool(_skip_reason or _reachable_skip),
    reason=_skip_reason or _reachable_skip,
)


@skip_live
class TestLiveSupermicro(unittest.TestCase):
    """Live read-only tests against Supermicro BMC."""

    @classmethod
    def setUpClass(cls):
        from redfish_mcp.redfish import RedfishClient

        cls.client = RedfishClient(
            host=_host,
            user=_user,
            password=_password,
            verify_tls=False,
            timeout_s=30,
        )
        cls.endpoint = cls.client.discover_system()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_system_discovery(self):
        assert self.endpoint.system_path.startswith("/redfish/v1/Systems/")
        assert self.endpoint.base_url == f"https://{_host}"

    def test_system_info(self):
        system = self.client.get_json(self.endpoint.system_url)
        assert system.get("Manufacturer") is not None
        assert system.get("Model") is not None
        assert system.get("PowerState") in ("On", "Off", "PoweringOn", "PoweringOff")

    # -- Chassis Telemetry --

    def test_collect_power_info(self):
        from redfish_mcp.chassis_telemetry import collect_power_info

        result = collect_power_info(self.client)
        assert result["psu_count"] > 0, f"Expected PSUs, got: {result}"
        psu = result["power_supplies"][0]
        assert psu.get("Name") is not None
        assert psu.get("Status") is not None
        assert not result["errors"], f"Errors: {result['errors']}"

    def test_collect_thermal_info(self):
        from redfish_mcp.chassis_telemetry import collect_thermal_info

        result = collect_thermal_info(self.client)
        assert result["temperature_count"] > 0, f"Expected temps, got: {result}"
        temp = result["temperatures"][0]
        assert temp.get("Name") is not None
        # ReadingCelsius may be None for offline sensors; assert key exists
        assert "ReadingCelsius" in temp
        assert not result["errors"], f"Errors: {result['errors']}"

    # -- System Inventory --

    def test_collect_processor_inventory(self):
        from redfish_mcp.system_inventory import collect_processor_inventory

        result = collect_processor_inventory(self.client, self.endpoint)
        assert result["count"] >= 1, f"Expected CPUs, got: {result}"
        cpu = result["processors"][0]
        assert cpu.get("Manufacturer") is not None
        assert cpu.get("TotalCores") is not None and cpu["TotalCores"] > 0
        assert not result["errors"], f"Errors: {result['errors']}"

    def test_collect_memory_inventory(self):
        from redfish_mcp.system_inventory import collect_memory_inventory

        result = collect_memory_inventory(self.client, self.endpoint)
        assert result["populated_count"] > 0, f"Expected DIMMs, got: {result}"
        assert result["total_capacity_gib"] > 0
        assert result["summary"]["total_slots"] > 0
        assert not result["errors"], f"Errors: {result['errors']}"

    def test_collect_pcie_inventory(self):
        from redfish_mcp.system_inventory import collect_pcie_inventory

        result = collect_pcie_inventory(self.client, self.endpoint)
        # H100 machine should have PCIe devices; some BMCs may not expose this
        if result["count"] == 0 and result["errors"]:
            pytest.skip(f"PCIe not exposed by this BMC: {result['errors']}")
        assert result["count"] > 0, f"Expected PCIe devices, got: {result}"

    # -- Manager Info --

    def test_collect_manager_info(self):
        from redfish_mcp.manager_info import collect_manager_info

        result = collect_manager_info(self.client)
        assert result["manager"] is not None, f"Expected manager, got: {result}"
        mgr = result["manager"]
        assert mgr.get("FirmwareVersion") is not None
        assert mgr.get("ManagerType") is not None
        assert not result["errors"], f"Errors: {result['errors']}"

    def test_collect_manager_ethernet(self):
        from redfish_mcp.manager_info import collect_manager_ethernet

        result = collect_manager_ethernet(self.client)
        assert result["count"] > 0, f"Expected interfaces, got: {result}"
        iface = result["interfaces"][0]
        assert iface.get("MACAddress") is not None
        assert "IPv4Addresses" in iface
        assert not result["errors"], f"Errors: {result['errors']}"

    # -- Existing Modules --

    def test_collect_drive_inventory(self):
        from redfish_mcp.inventory import collect_drive_inventory

        result = collect_drive_inventory(self.client, self.endpoint, nvme_only=True)
        # NVMe drives expected on H100 node
        if result["count"] == 0 and not result["errors"]:
            pytest.skip("No NVMe drives found (may be expected for some configs)")
        assert result["count"] > 0, f"Expected drives, got: {result}"

    def test_collect_firmware_inventory(self):
        from redfish_mcp.firmware_inventory import collect_firmware_inventory

        result = collect_firmware_inventory(self.client, self.endpoint)
        assert result["component_count"] > 0, f"Expected firmware, got: {result}"
        assert len(result["by_category"]) > 0
        # Should have at least BIOS or BMC category
        categories = set(result["by_category"].keys())
        assert categories & {"bios", "bmc"}, f"Expected bios/bmc categories, got: {categories}"


@skip_live
class TestLiveGetInfoConsolidated(unittest.TestCase):
    """Test the consolidated redfish_get_info MCP tool with all info_types."""

    @classmethod
    def setUpClass(cls):
        from redfish_mcp.mcp_server import create_mcp_app

        _, cls.tools = create_mcp_app()

    def test_info_all(self):
        """Call redfish_get_info with info_types=["all"] and verify all sections."""
        import asyncio

        result = asyncio.run(
            self.tools["redfish_get_info"](
                host=_host,
                user=_user,
                password=_password,
                info_types=["all"],
                verify_tls=False,
                timeout_s=30,
            )
        )
        assert result.get("ok") is True, f"Expected ok=True, got: {result}"

        # Standard sections
        assert "system" in result, f"Missing 'system' in result keys: {list(result.keys())}"
        assert "boot" in result

        # New sections
        for section in [
            "power",
            "thermal",
            "processors",
            "memory",
            "manager_info",
            "manager_ethernet",
        ]:
            assert section in result, f"Missing '{section}' in result keys: {list(result.keys())}"

    def test_info_single_type(self):
        """Test requesting just one new info_type."""
        import asyncio

        result = asyncio.run(
            self.tools["redfish_get_info"](
                host=_host,
                user=_user,
                password=_password,
                info_types=["processors"],
                verify_tls=False,
                timeout_s=30,
            )
        )
        assert result.get("ok") is True
        assert "processors" in result
        assert "system" not in result  # Should NOT include unrequested types


if __name__ == "__main__":
    unittest.main()
