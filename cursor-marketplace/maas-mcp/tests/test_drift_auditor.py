"""Tests for drift_auditor comparison functions with realistic data."""

from maas_mcp.drift_auditor import compare_bios, compare_nics, compare_storage


class TestCompareNics:
    def test_matching_interfaces(self):
        iface = {"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0", "type": "physical"}
        m1 = {"interfaces": [iface.copy()]}
        m2 = {"interfaces": [iface.copy()]}
        result = compare_nics(m1, m2)
        assert len(result["matches"]) == 1
        assert result["matches"][0]["mac_address"] == "aa:bb:cc:dd:ee:01"
        assert result["differences"] == []

    def test_different_interface_names(self):
        m1 = {
            "interfaces": [
                {"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0", "type": "physical"}
            ]
        }
        m2 = {
            "interfaces": [{"mac_address": "aa:bb:cc:dd:ee:01", "name": "eth0", "type": "physical"}]
        }
        result = compare_nics(m1, m2)
        assert len(result["differences"]) == 1
        assert "name" in result["differences"][0]["differences"]

    def test_extra_interfaces(self):
        m1 = {
            "interfaces": [
                {"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0"},
                {"mac_address": "aa:bb:cc:dd:ee:02", "name": "enp2s0"},
            ]
        }
        m2 = {"interfaces": [{"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0"}]}
        result = compare_nics(m1, m2)
        assert len(result["only_in_machine1"]) == 1
        assert result["only_in_machine1"][0]["mac_address"] == "aa:bb:cc:dd:ee:02"

    def test_non_list_interfaces_coerced(self):
        m1 = {"interfaces": {"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0"}}
        m2 = {"interfaces": [{"mac_address": "aa:bb:cc:dd:ee:01", "name": "enp1s0"}]}
        result = compare_nics(m1, m2)
        assert len(result["matches"]) == 1


class TestCompareStorage:
    def test_matching_devices(self):
        dev = {"serial": "SN123", "name": "sda", "size": 1000000000, "model": "Samsung"}
        m1 = {"block_devices": [dev.copy()]}
        m2 = {"block_devices": [dev.copy()]}
        result = compare_storage(m1, m2)
        assert len(result["matches"]) == 1
        assert result["differences"] == []

    def test_size_mismatch(self):
        m1 = {"block_devices": [{"serial": "SN123", "name": "sda", "size": 1000}]}
        m2 = {"block_devices": [{"serial": "SN123", "name": "sda", "size": 2000}]}
        result = compare_storage(m1, m2)
        assert len(result["differences"]) == 1
        assert "size" in result["differences"][0]["differences"]

    def test_extra_device_in_machine2(self):
        m1 = {"block_devices": [{"serial": "SN1", "name": "sda"}]}
        m2 = {
            "block_devices": [
                {"serial": "SN1", "name": "sda"},
                {"serial": "SN2", "name": "sdb"},
            ]
        }
        result = compare_storage(m1, m2)
        assert len(result["only_in_machine2"]) == 1

    def test_fallback_to_name_key(self):
        m1 = {"block_devices": [{"name": "nvme0n1", "size": 500}]}
        m2 = {"block_devices": [{"name": "nvme0n1", "size": 500}]}
        result = compare_storage(m1, m2)
        assert len(result["matches"]) == 1


class TestCompareBios:
    def test_identical_settings(self):
        m1 = {"bios_settings": {"boot_mode": "UEFI", "sr_iov": "enabled"}}
        m2 = {"bios_settings": {"boot_mode": "UEFI", "sr_iov": "enabled"}}
        result = compare_bios(m1, m2)
        assert len(result["matches"]) == 2
        assert result["differences"] == []

    def test_value_difference(self):
        m1 = {"bios_settings": {"boot_mode": "UEFI"}}
        m2 = {"bios_settings": {"boot_mode": "Legacy"}}
        result = compare_bios(m1, m2)
        assert len(result["differences"]) == 1
        assert result["differences"][0]["machine1"] == "UEFI"
        assert result["differences"][0]["machine2"] == "Legacy"

    def test_missing_bios_settings(self):
        m1 = {"bios_settings": None}
        m2 = {"bios_settings": {"boot_mode": "UEFI"}}
        result = compare_bios(m1, m2)
        assert "boot_mode" in result["only_in_machine2"]

    def test_non_dict_bios_treated_as_empty(self):
        m1 = {"bios_settings": "invalid"}
        m2 = {"bios_settings": {}}
        result = compare_bios(m1, m2)
        assert result["matches"] == []
        assert result["differences"] == []
