"""Tests for netbox_helper cross-reference utilities."""

from maas_mcp.netbox_helper import (
    find_device_by_provider_id,
    fuzzy_match_machine,
    validate_link,
)


class TestFindDeviceByProviderId:
    def test_exact_match(self):
        devices = [
            {"name": "gpu-01", "custom_fields": {"Provider_Machine_ID": "abc123"}},
            {"name": "gpu-02", "custom_fields": {"Provider_Machine_ID": "def456"}},
        ]
        result = find_device_by_provider_id("abc123", devices)
        assert result is not None
        assert result["name"] == "gpu-01"

    def test_case_insensitive(self):
        devices = [{"name": "gpu-01", "custom_fields": {"Provider_Machine_ID": "ABC123"}}]
        result = find_device_by_provider_id("abc123", devices)
        assert result is not None

    def test_not_found(self):
        devices = [{"name": "gpu-01", "custom_fields": {"Provider_Machine_ID": "xyz"}}]
        assert find_device_by_provider_id("abc123", devices) is None

    def test_empty_custom_fields(self):
        devices = [{"name": "gpu-01", "custom_fields": None}]
        assert find_device_by_provider_id("abc123", devices) is None

    def test_no_custom_fields_key(self):
        devices = [{"name": "gpu-01"}]
        assert find_device_by_provider_id("abc123", devices) is None


class TestFuzzyMatchMachine:
    def test_hostname_provider_id_match(self):
        machine = {"hostname": "abc123", "system_id": "abc123", "interfaces": []}
        devices = [{"custom_fields": {"Provider_Machine_ID": "abc123"}}]
        result = fuzzy_match_machine(machine, devices)
        assert result["confidence"] == "high"
        assert result["method"] == "hostname_provider_id"

    def test_mac_address_match(self):
        machine = {
            "hostname": "test-machine",
            "system_id": "sid1",
            "interfaces": [{"mac_address": "AA:BB:CC:DD:EE:FF"}],
        }
        devices = [
            {
                "custom_fields": {},
                "interfaces": [{"mac_address": "aa:bb:cc:dd:ee:ff"}],
            }
        ]
        result = fuzzy_match_machine(machine, devices)
        assert result["confidence"] == "medium"
        assert result["method"] == "mac_address"

    def test_ip_address_match(self):
        machine = {
            "hostname": "test-machine",
            "system_id": "sid1",
            "interfaces": [{"ip_addresses": [{"ip": "10.0.0.1"}]}],
        }
        devices = [
            {
                "custom_fields": {},
                "interfaces": [{"ip_addresses": [{"address": "10.0.0.1/24"}]}],
            }
        ]
        result = fuzzy_match_machine(machine, devices)
        assert result["confidence"] == "low"
        assert result["method"] == "ip_address"

    def test_no_match(self):
        machine = {"hostname": "ghost", "system_id": "sid1", "interfaces": []}
        devices = [{"custom_fields": {"Provider_Machine_ID": "other"}, "interfaces": []}]
        result = fuzzy_match_machine(machine, devices)
        assert result["match"] is None
        assert result["confidence"] == "none"

    def test_hostname_mismatch_warning(self):
        machine = {
            "hostname": "wrong-name",
            "system_id": "sid1",
            "interfaces": [{"mac_address": "aa:bb:cc:dd:ee:ff"}],
        }
        devices = [
            {
                "custom_fields": {"Provider_Machine_ID": "correct-name"},
                "interfaces": [{"mac_address": "aa:bb:cc:dd:ee:ff"}],
            }
        ]
        result = fuzzy_match_machine(machine, devices)
        assert result["match"] is not None
        assert any("does not match" in w for w in result["warnings"])


class TestValidateLink:
    def test_valid_hostname_match(self):
        maas = {"hostname": "abc123", "system_id": "abc123", "interfaces": []}
        netbox = {"custom_fields": {"Provider_Machine_ID": "abc123"}, "interfaces": []}
        result = validate_link(maas, netbox)
        assert result["valid"] is True
        assert result["matches"]["hostname"] is True

    def test_mac_address_validation(self):
        maas = {
            "hostname": "different",
            "system_id": "sid1",
            "interfaces": [{"mac_address": "AA:BB:CC:DD:EE:FF"}],
        }
        netbox = {
            "custom_fields": {"Provider_Machine_ID": "other"},
            "interfaces": [{"mac_address": "aa:bb:cc:dd:ee:ff"}],
        }
        result = validate_link(maas, netbox)
        assert result["valid"] is True
        assert result["matches"]["mac_addresses"] is True
        assert "AA:BB:CC:DD:EE:FF".lower() in result["matches"]["common_macs"]

    def test_no_provider_id_warning(self):
        maas = {"hostname": "test", "system_id": "sid1", "interfaces": []}
        netbox = {"custom_fields": {}, "interfaces": []}
        result = validate_link(maas, netbox)
        assert any("no Provider_Machine_ID" in w for w in result["warnings"])

    def test_invalid_no_matches(self):
        maas = {"hostname": "a", "system_id": "b", "interfaces": []}
        netbox = {"custom_fields": {"Provider_Machine_ID": "c"}, "interfaces": []}
        result = validate_link(maas, netbox)
        assert result["valid"] is False
