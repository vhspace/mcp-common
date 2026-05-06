"""Unit tests for core modules."""

from typing import ClassVar

import pytest

from redfish_mcp._util import norm, require
from redfish_mcp.bios_diff import diff_attributes
from redfish_mcp.boot import (
    get_allowable_targets,
    pick_target,
)
from redfish_mcp.redfish import (
    MAX_HOST_CHASSIS,
    _iter_chassis_segments,
    _pick_host_manager,
    filter_hgx_pcie_chassis,
    filter_host_chassis,
)


class TestUtil:
    def test_norm(self):
        assert norm("BiosSetup") == "biossetup"
        assert norm("Re_SizeBAR_00B2") == "resizebar00b2"
        assert norm("  UPPER lower 123  ") == "upperlower123"

    def test_require(self):
        assert require("test", "value") == "value"
        with pytest.raises(SystemExit):
            require("test", None)
        with pytest.raises(SystemExit):
            require("test", "")


class TestBoot:
    def test_pick_target_with_allowable(self):
        allowable = ["BiosSetup", "Pxe", "Hdd"]
        chosen, attempted = pick_target("bios", allowable)
        assert chosen == "BiosSetup"
        assert "BiosSetup" in (attempted or [])

    def test_pick_target_alias_matching(self):
        allowable = ["Setup", "Network"]
        chosen, _attempted = pick_target("bios", allowable)
        assert chosen == "Setup"  # Should match BIOS alias

    def test_pick_target_no_allowable(self):
        chosen, attempted = pick_target("bios", None)
        assert chosen == "BiosSetup"  # First alias
        assert attempted is None

    def test_pick_target_fallback(self):
        allowable = ["Unknown1", "Unknown2"]
        chosen, _attempted = pick_target("something", allowable)
        assert chosen == "Unknown1"  # Falls back to first allowable

    def test_get_allowable_targets_standard(self):
        system = {
            "Boot": {
                "BootSourceOverrideTarget@Redfish.AllowableValues": ["BiosSetup", "Pxe", "Hdd"]
            }
        }
        targets = get_allowable_targets(system)
        assert targets == ["BiosSetup", "Pxe", "Hdd"]

    def test_get_allowable_targets_in_system_root(self):
        system = {"BootSourceOverrideTarget@Redfish.AllowableValues": ["Setup", "Network"]}
        targets = get_allowable_targets(system)
        assert targets == ["Setup", "Network"]

    def test_get_allowable_targets_none(self):
        system = {"Boot": {}}
        targets = get_allowable_targets(system)
        assert targets is None


class TestIterChassisSegments:
    """Test _iter_chassis_segments() extracts (member, segment) tuples."""

    def test_basic(self):
        members = [
            {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
            {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
        ]
        result = _iter_chassis_segments(members)
        assert len(result) == 2
        assert result[0][1] == "System.Embedded.1"
        assert result[1][1] == "HGX_GPU_0"

    def test_trailing_slash(self):
        members = [{"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0/"}]
        result = _iter_chassis_segments(members)
        assert result[0][1] == "HGX_GPU_0"

    def test_skips_non_dict(self):
        members = [{"@odata.id": "/redfish/v1/Chassis/1"}, "not_a_dict", 42]
        result = _iter_chassis_segments(members)
        assert len(result) == 1

    def test_skips_missing_odata_id(self):
        members = [{"@odata.id": "/redfish/v1/Chassis/1"}, {"no_id": True}]
        result = _iter_chassis_segments(members)
        assert len(result) == 1

    def test_empty_list(self):
        assert _iter_chassis_segments([]) == []


class TestFilterHostChassis:
    """Test filter_host_chassis() with realistic B300 chassis collections."""

    B300_MEMBERS: ClassVar[list[dict[str, str]]] = [
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
        {"@odata.id": "/redfish/v1/Chassis/Enclosure.Internal.0-0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_1"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_2"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_3"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_4"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_5"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_6"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_7"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_1"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_2"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_3"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_PCIeSwitch_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_PCIeSwitch_1"},
        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_1"},
        {"@odata.id": "/redfish/v1/Chassis/IRoT_NVSwitch_0"},
    ]

    def test_b300_filters_to_host_only(self):
        result = filter_host_chassis(self.B300_MEMBERS)
        ids = [m["@odata.id"] for m in result]
        assert len(result) == 2
        assert "/redfish/v1/Chassis/System.Embedded.1" in ids
        assert "/redfish/v1/Chassis/Enclosure.Internal.0-0" in ids

    def test_single_chassis_passthrough(self):
        members = [{"@odata.id": "/redfish/v1/Chassis/1"}]
        result = filter_host_chassis(members)
        assert len(result) == 1

    def test_empty_list(self):
        assert filter_host_chassis([]) == []

    def test_max_chassis_cap(self):
        members = [{"@odata.id": f"/redfish/v1/Chassis/Shelf_{i}"} for i in range(20)]
        result = filter_host_chassis(members, max_chassis=5)
        assert len(result) == 5

    def test_default_cap(self):
        members = [{"@odata.id": f"/redfish/v1/Chassis/Shelf_{i}"} for i in range(15)]
        result = filter_host_chassis(members)
        assert len(result) == MAX_HOST_CHASSIS

    def test_malformed_members_skipped(self):
        members = [
            {"@odata.id": "/redfish/v1/Chassis/1"},
            "not_a_dict",
            {"no_odata_id": True},
        ]
        result = filter_host_chassis(members)
        assert len(result) == 1


class TestFilterHgxPcieChassis:
    """Test filter_hgx_pcie_chassis() with B300 chassis collections."""

    B300_MEMBERS: ClassVar[list[dict[str, str]]] = [
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_1"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_ConnectX_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_0"},
        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/IRoT_NVSwitch_0"},
    ]

    def test_selects_gpu_and_connectx(self):
        result = filter_hgx_pcie_chassis(self.B300_MEMBERS)
        ids = [m["@odata.id"] for m in result]
        assert "/redfish/v1/Chassis/HGX_GPU_0" in ids
        assert "/redfish/v1/Chassis/HGX_GPU_1" in ids
        assert "/redfish/v1/Chassis/HGX_ConnectX_0" in ids

    def test_excludes_baseboard(self):
        result = filter_hgx_pcie_chassis(self.B300_MEMBERS)
        ids = [m["@odata.id"] for m in result]
        assert "/redfish/v1/Chassis/HGX_GPU_Baseboard_0" not in ids

    def test_excludes_non_pcie(self):
        result = filter_hgx_pcie_chassis(self.B300_MEMBERS)
        ids = [m["@odata.id"] for m in result]
        assert "/redfish/v1/Chassis/System.Embedded.1" not in ids
        assert "/redfish/v1/Chassis/HGX_NVSwitch_0" not in ids
        assert "/redfish/v1/Chassis/ERoT_GPU_0" not in ids
        assert "/redfish/v1/Chassis/IRoT_NVSwitch_0" not in ids

    def test_max_members_cap(self):
        members = [{"@odata.id": f"/redfish/v1/Chassis/HGX_GPU_{i}"} for i in range(30)]
        result = filter_hgx_pcie_chassis(members, max_members=5)
        assert len(result) == 5

    def test_empty_list(self):
        assert filter_hgx_pcie_chassis([]) == []


class TestPickHostManager:
    """Test _pick_host_manager() for B300 multi-manager routing."""

    def test_single_manager(self):
        members = [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"}]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/iDRAC.Embedded.1"

    def test_single_hgx_manager(self):
        members = [{"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"}]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/HGX_BMC_0"

    def test_dual_managers_idrac_first(self):
        members = [
            {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
            {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
        ]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/iDRAC.Embedded.1"

    def test_dual_managers_hgx_first(self):
        """On B300 where HGX_BMC_0 is listed first, iDRAC should still win."""
        members = [
            {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
            {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
        ]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/iDRAC.Embedded.1"

    def test_only_hgx_managers(self):
        """When no iDRAC exists, falls back to Members[0]."""
        members = [
            {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
            {"@odata.id": "/redfish/v1/Managers/HGX_BMC_1"},
        ]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/HGX_BMC_0"

    def test_non_dell_single_manager(self):
        members = [{"@odata.id": "/redfish/v1/Managers/1"}]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/1"

    def test_non_dell_multiple_managers(self):
        members = [
            {"@odata.id": "/redfish/v1/Managers/BMC"},
            {"@odata.id": "/redfish/v1/Managers/CMC"},
        ]
        result = _pick_host_manager(members)
        assert result["@odata.id"] == "/redfish/v1/Managers/BMC"

    def test_trailing_slash(self):
        members = [
            {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0/"},
            {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/"},
        ]
        result = _pick_host_manager(members)
        assert result["@odata.id"].rstrip("/").endswith("iDRAC.Embedded.1")

    def test_malformed_single_member(self):
        members = [{"Name": "Manager"}]
        with pytest.raises(RuntimeError, match="Unexpected Managers Members"):
            _pick_host_manager(members)

    def test_empty_list_raises(self):
        with pytest.raises((RuntimeError, IndexError)):
            _pick_host_manager([])


class TestBiosDiff:
    def test_diff_attributes_only_a(self):
        a = {"key1": "value1", "key2": "value2"}
        b = {"key2": "value2"}
        diff = diff_attributes(a, b)
        assert len(diff["only_a"]) == 1
        assert diff["only_a"][0]["key"] == "key1"
        assert len(diff["only_b"]) == 0
        assert len(diff["different"]) == 0
        assert len(diff["same"]) == 1

    def test_diff_attributes_only_b(self):
        a = {"key1": "value1"}
        b = {"key1": "value1", "key2": "value2"}
        diff = diff_attributes(a, b)
        assert len(diff["only_a"]) == 0
        assert len(diff["only_b"]) == 1
        assert diff["only_b"][0]["key"] == "key2"

    def test_diff_attributes_different(self):
        a = {"key1": "value1", "key2": "old"}
        b = {"key1": "value1", "key2": "new"}
        diff = diff_attributes(a, b)
        assert len(diff["different"]) == 1
        assert diff["different"][0]["key"] == "key2"
        assert diff["different"][0]["value_a"] == "old"
        assert diff["different"][0]["value_b"] == "new"
        assert len(diff["same"]) == 1

    def test_diff_attributes_keys_like_filter(self):
        a = {"MMIO_Setting": "value1", "Other_Setting": "value2", "MMIO_Other": "value3"}
        b = {"MMIO_Setting": "value1", "Other_Setting": "different", "MMIO_Other": "value3"}
        diff = diff_attributes(a, b, keys_like="MMIO")
        # Should only consider keys containing "MMIO" (case insensitive)
        assert diff["counts"]["total_keys"] == 2
        assert len(diff["same"]) == 2

    def test_diff_attributes_counts(self):
        a = {"only_a": "1", "diff": "old", "same": "unchanged"}
        b = {"only_b": "2", "diff": "new", "same": "unchanged"}
        diff = diff_attributes(a, b)
        counts = diff["counts"]
        assert counts["only_a"] == 1
        assert counts["only_b"] == 1
        assert counts["different"] == 1
        assert counts["same"] == 1
        assert counts["total_keys"] == 4
