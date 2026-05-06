"""Tests for hardware_docs module."""

from redfish_mcp.hardware_docs import (
    get_bios_info,
    get_firmware_update_info,
    match_hardware,
)


class TestMatchHardware:
    def test_no_model_returns_none(self):
        assert match_hardware("Supermicro", None) is None

    def test_unknown_model_returns_none(self):
        result = match_hardware("UnknownVendor", "UnknownModel-999")
        assert result is None

    def test_sys_121h_tnr_matches(self):
        result = match_hardware("Supermicro", "SYS-121H-TNR")
        assert result is not None
        assert result["hardware"]["model"] == "SYS-121H-TNR"
        assert "power_management" in result
        pm = result["power_management"]["maas_compatibility"]
        assert pm["recommended_power_type"] == "ipmi"
        assert any("AccountTypes" in n for n in pm["notes"])
        assert len(pm["known_false_alarms"]) >= 1
        assert len(pm["troubleshooting"]) >= 1

    def test_nvidia_hgx_baseboard_matches_by_manufacturer_model_na(self):
        """NVIDIA HGX BMC reports Model='NA' — match via redfish_identification."""
        result = match_hardware("NVIDIA", "NA")
        assert result is not None
        assert result["hardware"]["vendor"] == "NVIDIA"
        assert result["hardware"]["model"] == "HGX-Baseboard"
        assert result["hardware"]["family"] == "HGX"

    def test_nvidia_hgx_baseboard_has_expected_fields(self):
        result = match_hardware("NVIDIA", "NA")
        assert result is not None
        assert result["redfish_endpoints"]["system"] == "/redfish/v1/Systems/HGX_Baseboard_0"
        assert result["redfish_endpoints"]["manager"] == "/redfish/v1/Managers/BMC"
        assert result["redfish_capabilities"]["gpu_processors"] is True
        assert result["redfish_capabilities"]["bios_settings"] is False
        assert result["gpu_info"]["gpu_count"] == 8
        assert len(result["known_limitations"]) >= 5

    def test_nvidia_hgx_baseboard_docs(self):
        result = match_hardware("NVIDIA", "NA")
        assert result is not None
        assert "documentation" in result
        assert "nvidia.com" in result["documentation"]["manual"]

    def test_nvidia_case_insensitive_manufacturer(self):
        result = match_hardware("nvidia", "NA")
        assert result is not None
        assert result["hardware"]["vendor"] == "NVIDIA"

    def test_nvidia_unknown_model_no_match(self):
        """Non-'NA' unknown NVIDIA model should not match HGX-Baseboard."""
        result = match_hardware("NVIDIA", "SomeOtherGPU-9000")
        assert result is None


class TestGigabyteHardware:
    def test_gigabyte_generic_matches_by_vendor(self):
        result = match_hardware("Giga Computing", "SomeUnknownModel")
        assert result is not None
        assert result["hardware"]["vendor"] == "Giga Computing"
        assert "bmc_info" in result
        assert result["bmc_info"]["type"] == "AMI MegaRAC"

    def test_gigabyte_b200_matches(self):
        result = match_hardware("Giga Computing", "B200-180GB-SXM")
        assert result is not None
        assert result["hardware"]["model"] == "B200-180GB-SXM"
        assert result["hardware"]["gpu_slots"] == 8
        assert "bmc_info" in result
        assert result["bmc_info"]["host_bmc"]["firmware_version"] == "13.06.16"

    def test_gigabyte_b200_has_redfish_endpoints(self):
        result = match_hardware("Giga Computing", "B200-180GB-SXM")
        assert result is not None
        assert result["redfish_endpoints"]["host_system"] == "/redfish/v1/Systems/Self"
        assert result["redfish_endpoints"]["hgx_system"] == "/redfish/v1/Systems/HGX_Baseboard_0"
        assert result["redfish_endpoints"]["host_manager"] == "/redfish/v1/Managers/Self"

    def test_gigabyte_b200_has_gpu_info(self):
        result = match_hardware("Giga Computing", "B200-180GB-SXM")
        assert result is not None
        assert result["gpu_info"]["gpu_count"] == 8
        assert result["gpu_info"]["gpu_model"] == "B200 180GB HBM3e"
        assert len(result["gpu_info"]["processor_ids"]) == 8

    def test_gigabyte_b200_dual_manager_structure(self):
        result = match_hardware("Giga Computing", "B200-180GB-SXM")
        assert result is not None
        bmc = result["bmc_info"]
        assert bmc["host_bmc"]["manager_id"] == "Self"
        assert bmc["hgx_bmc"]["manager_id"] == "HGX_BMC_0"
        assert bmc["hgx_bmc"]["model"] == "OpenBmc"
        assert bmc["fabric_manager"]["manager_id"] == "HGX_FabricManager_0"

    def test_gigabyte_b200_chassis_members(self):
        result = match_hardware("Giga Computing", "B200-180GB-SXM")
        assert result is not None
        chassis = result["chassis_members"]
        assert chassis["total_count"] == 36
        assert len(chassis["gpu_modules"]) == 8
        assert len(chassis["nvswitch"]) == 2


class TestGetBiosInfo:
    def test_no_known_versions(self):
        result = get_bios_info({}, "1.0")
        assert result["current_version"] == "1.0"
        assert result["known_versions"] == []
        assert result["is_latest"] is None

    def test_no_current_bios(self):
        result = get_bios_info({"bios_versions": {"3.7a": {}}}, None)
        assert result["known_versions"] == []
        assert result["is_latest"] is None

    def test_known_version_extraction(self):
        hw = {
            "bios_versions": {
                "3.7a": {"status": "stable", "changes": ["fix1"]},
                "3.8a": {"status": "latest", "changes": ["fix2"]},
            }
        }
        result = get_bios_info(hw, "BIOS Date: 09/20/2025 Ver 3.7a")
        assert "3.7a" in result["known_versions"]
        assert "3.8a" in result["known_versions"]
        assert result["is_latest"] is False
        assert result["recommended_version"] == "3.8a"

    def test_latest_version_detected(self):
        hw = {"bios_versions": {"3.8a": {"status": "latest", "changes": []}}}
        result = get_bios_info(hw, "BIOS Date: 01/01/2026 Ver 3.8a")
        assert result["is_latest"] is True


class TestGetFirmwareUpdateInfo:
    def test_unknown_hardware(self):
        result = get_firmware_update_info("Unknown", "Unknown-999", "1.0")
        assert result["updates_available"] is False
        assert "not in database" in result.get("note", "")

    def test_online_check_older(self):
        online_result = {
            "ok": True,
            "latest_version": "3.8a",
            "download_url": "https://example.com",
        }
        result = get_firmware_update_info(
            "Supermicro", "SomeModel", "BIOS Date: 01/01/2025 Ver 3.7a", online_result
        )
        assert result["updates_available"] is True
        assert result["source"] == "online"
        assert any("update available" in r for r in result["recommendations"])

    def test_online_check_same(self):
        online_result = {"ok": True, "latest_version": "3.8a"}
        result = get_firmware_update_info(
            "Supermicro", "SomeModel", "BIOS Date: 01/01/2025 Ver 3.8a", online_result
        )
        assert result["updates_available"] is False
        assert any("up to date" in r for r in result["recommendations"])
