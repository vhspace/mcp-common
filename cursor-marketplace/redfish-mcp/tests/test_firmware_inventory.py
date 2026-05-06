"""Tests for firmware_inventory module."""

from redfish_mcp.firmware_inventory import get_vendor_errata_urls


class TestGetVendorErrataUrls:
    def test_supermicro(self):
        result = get_vendor_errata_urls("Supermicro")
        assert result["vendor"] == "Supermicro"
        assert result["security_bulletin_url"] is not None
        assert len(result["errata_urls"]) > 0

    def test_dell(self):
        result = get_vendor_errata_urls("Dell Inc.")
        assert result["vendor"] == "Dell Inc."
        assert result["security_bulletin_url"] is not None

    def test_hpe(self):
        result = get_vendor_errata_urls("HPE")
        assert result["security_bulletin_url"] is not None

    def test_lenovo(self):
        result = get_vendor_errata_urls("Lenovo")
        assert len(result["errata_urls"]) > 0

    def test_unknown_vendor(self):
        result = get_vendor_errata_urls("UnknownCorp")
        assert len(result["errata_urls"]) == 0
        assert any("manually" in n for n in result["notes"])

    def test_none_manufacturer(self):
        result = get_vendor_errata_urls(None)
        assert result["vendor"] is None
        assert len(result["errata_urls"]) == 0
