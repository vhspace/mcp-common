"""Tests for smart BIOS diff with semantic matching."""

from redfish_mcp.bios_diff import (
    diff_attributes_smart,
    get_bios_attributes,
    normalize_attribute_name,
)


class TestNormalizeAttributeName:
    def test_strips_hex_suffix(self):
        assert normalize_attribute_name("SMTControl_0037") == "SMTControl"
        assert normalize_attribute_name("IOMMU_0196") == "IOMMU"
        assert normalize_attribute_name("Above4GDecoding_00B1") == "Above4GDecoding"

    def test_no_suffix_unchanged(self):
        assert normalize_attribute_name("SMTControl") == "SMTControl"
        assert normalize_attribute_name("IOMMU") == "IOMMU"

    def test_non_hex_suffix_unchanged(self):
        assert normalize_attribute_name("Setting_GHIJ") == "Setting_GHIJ"

    def test_longer_suffix_unchanged(self):
        assert normalize_attribute_name("Setting_00B1A") == "Setting_00B1A"

    def test_shorter_suffix_unchanged(self):
        assert normalize_attribute_name("Setting_0B1") == "Setting_0B1"


class TestDiffAttributesSmart:
    def test_identical_attributes(self):
        a = {"SMTControl": "Enabled", "IOMMU": "Auto"}
        b = {"SMTControl": "Enabled", "IOMMU": "Auto"}
        result = diff_attributes_smart(a, b)
        assert result["counts"]["matched"] == 2
        assert result["counts"]["matched_same"] == 2
        assert result["counts"]["matched_different"] == 0
        assert len(result["only_a"]) == 0
        assert len(result["only_b"]) == 0

    def test_semantic_matching_across_versions(self):
        a = {"SMTControl_0037": "Enabled", "IOMMU_0196": "Auto"}
        b = {"SMTControl": "Enabled", "IOMMU": "Auto"}
        result = diff_attributes_smart(a, b)
        assert result["counts"]["matched"] == 2
        assert result["counts"]["matched_same"] == 2
        assert len(result["only_a"]) == 0
        assert len(result["only_b"]) == 0

    def test_different_values_detected(self):
        a = {"SMTControl_0037": "Enabled"}
        b = {"SMTControl": "Disabled"}
        result = diff_attributes_smart(a, b)
        assert result["counts"]["matched_different"] == 1
        match = result["matched"][0]
        assert match["value_a"] == "Enabled"
        assert match["value_b"] == "Disabled"
        assert match["values_match"] is False

    def test_critical_differences_flagged(self):
        a = {"SMTControl_0037": "Enabled"}
        b = {"SMTControl": "Disabled"}
        result = diff_attributes_smart(a, b)
        assert len(result["critical_differences"]) == 1
        crit = result["critical_differences"][0]
        assert crit["is_critical"] is True
        assert crit["category"] == "CPU"

    def test_critical_same_not_flagged(self):
        a = {"SMTControl": "Enabled"}
        b = {"SMTControl": "Enabled"}
        result = diff_attributes_smart(a, b)
        assert len(result["critical_differences"]) == 0

    def test_only_a_attributes(self):
        a = {"UniqueSetting_0037": "val"}
        b = {}
        result = diff_attributes_smart(a, b)
        assert len(result["only_a"]) == 1
        assert result["only_a"][0]["normalized_name"] == "UniqueSetting"

    def test_only_b_attributes(self):
        a = {}
        b = {"NewSetting": "val"}
        result = diff_attributes_smart(a, b)
        assert len(result["only_b"]) == 1

    def test_keys_like_filter(self):
        a = {"SMTControl": "Enabled", "IOMMU": "Auto", "Other": "val"}
        b = {"SMTControl": "Disabled", "IOMMU": "Enabled", "Other": "val"}
        result = diff_attributes_smart(a, b, keys_like="SMT")
        assert result["counts"]["matched"] == 1
        assert result["matched"][0]["normalized_name"] == "SMTControl"

    def test_summary_present(self):
        a = {"A": "1", "B": "2"}
        b = {"A": "1", "C": "3"}
        result = diff_attributes_smart(a, b)
        assert "summary" in result
        assert "total_matched_attributes" in result["summary"]
        assert "note" in result["summary"]


class TestGetBiosAttributes:
    def test_missing_attributes_key(self):
        class MockClient:
            base_url = "https://host"

            def get_json_maybe(self, url):
                return ({"Name": "BIOS"}, None)

        class MockEndpoint:
            system_url = "https://host/redfish/v1/Systems/1"

        attrs, _url, err = get_bios_attributes(MockClient(), MockEndpoint())
        assert attrs is None
        assert "No Attributes" in err

    def test_bios_fetch_error(self):
        class MockClient:
            base_url = "https://host"

            def get_json_maybe(self, url):
                return (None, "404 Not Found")

        class MockEndpoint:
            system_url = "https://host/redfish/v1/Systems/1"

        attrs, _url, err = get_bios_attributes(MockClient(), MockEndpoint())
        assert attrs is None
        assert "404" in err

    def test_successful_extraction(self):
        class MockClient:
            base_url = "https://host"

            def get_json_maybe(self, url):
                return ({"Attributes": {"SMT": "Enabled"}}, None)

        class MockEndpoint:
            system_url = "https://host/redfish/v1/Systems/1"

        attrs, _url, err = get_bios_attributes(MockClient(), MockEndpoint())
        assert attrs == {"SMT": "Enabled"}
        assert err is None
