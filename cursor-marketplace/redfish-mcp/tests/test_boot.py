"""Additional boot module tests for edge cases."""

from redfish_mcp.boot import get_allowable_targets, pick_target


class TestPickTargetEdgeCases:
    def test_pxe_alias(self):
        allowable = ["UefiPxe", "Hdd"]
        chosen, attempted = pick_target("pxe", allowable)
        assert chosen == "UefiPxe"
        assert attempted is not None

    def test_network_alias(self):
        allowable = ["Network", "BiosSetup"]
        chosen, _ = pick_target("network", allowable)
        assert chosen == "Network"

    def test_hdd_alias(self):
        allowable = ["HardDrive", "Pxe"]
        chosen, _ = pick_target("hdd", allowable)
        assert chosen == "HardDrive"

    def test_cd_alias(self):
        allowable = ["Cdrom", "Pxe"]
        chosen, _ = pick_target("cd", allowable)
        assert chosen == "Cdrom"

    def test_case_insensitive_matching(self):
        allowable = ["biossetup", "pxe"]
        chosen, _ = pick_target("BIOS", allowable)
        assert chosen == "biossetup"

    def test_direct_value_when_no_alias(self):
        allowable = ["CustomTarget", "Other"]
        chosen, _ = pick_target("CustomTarget", allowable)
        # Falls back to first allowable when no match
        assert chosen in allowable


class TestGetAllowableTargetsEdgeCases:
    def test_with_non_list_value(self):
        system = {"Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": "not-a-list"}}
        assert get_allowable_targets(system) is None

    def test_with_non_string_items(self):
        system = {"Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": [1, 2, 3]}}
        assert get_allowable_targets(system) is None

    def test_dynamic_key_discovery(self):
        system = {
            "Boot": {
                "BootSourceOverrideTarget@Redfish.AllowableValues_custom": ["Pxe", "Hdd"],
            }
        }
        targets = get_allowable_targets(system)
        assert targets == ["Pxe", "Hdd"]

    def test_empty_boot_object(self):
        system = {"Boot": {}}
        assert get_allowable_targets(system) is None

    def test_no_boot_key(self):
        system = {"Status": {"Health": "OK"}}
        assert get_allowable_targets(system) is None
