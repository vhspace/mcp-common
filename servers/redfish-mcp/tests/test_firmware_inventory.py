"""Tests for firmware_inventory module."""

from unittest.mock import MagicMock

from redfish_mcp.firmware_inventory import collect_firmware_inventory, get_vendor_errata_urls
from redfish_mcp.redfish import RedfishEndpoint


def _mock_client(responses: dict[str, tuple]) -> MagicMock:
    """Mock RedfishClient returning canned responses keyed by URL suffix."""
    c = MagicMock()
    c.base_url = "https://10.0.0.1"

    def get_json_maybe(url: str):
        for suffix, (data, err) in sorted(responses.items(), key=lambda x: -len(x[0])):
            if url.endswith(suffix):
                return data, err
        return None, f"404 not found: {url}"

    c.get_json_maybe = MagicMock(side_effect=get_json_maybe)
    return c


class TestCollectFirmwareInventory:
    """by_category must hold compact {count, ids}, not duplicate component dicts."""

    def test_by_category_is_compact(self):
        responses = {
            "/redfish/v1/UpdateService/FirmwareInventory": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/BIOS"},
                        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/BMC"},
                        {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/NIC1"},
                    ]
                },
                None,
            ),
            "/FirmwareInventory/BIOS": (
                {"Id": "BIOS", "Name": "BIOS", "Version": "1.0"},
                None,
            ),
            "/FirmwareInventory/BMC": (
                {"Id": "BMC", "Name": "BMC Firmware", "Version": "2.0"},
                None,
            ),
            "/FirmwareInventory/NIC1": (
                {"Id": "NIC1", "Name": "Mellanox NIC", "Version": "3.0"},
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_firmware_inventory(c, ep)

        assert result["component_count"] == 3
        # Full component dicts still live under firmware_components.
        assert len(result["firmware_components"]) == 3
        assert any(comp["version"] == "1.0" for comp in result["firmware_components"])
        # by_category buckets are compact {count, ids} — no duplicated dicts.
        by_cat = result["by_category"]
        assert by_cat["bios"] == {"count": 1, "ids": ["BIOS"]}
        assert by_cat["bmc"] == {"count": 1, "ids": ["BMC"]}
        assert by_cat["network"] == {"count": 1, "ids": ["NIC1"]}
        assert all(set(v) == {"count", "ids"} for v in by_cat.values())


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
