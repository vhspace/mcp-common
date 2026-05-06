"""Tests for netbox_client module: NetboxClient and helpers."""

from maas_mcp.netbox_client import extract_ip


class TestExtractIp:
    def test_standard_address(self):
        device = {"primary_ip4": {"address": "192.168.229.16/24"}}
        assert extract_ip(device) == "192.168.229.16"

    def test_host_route(self):
        device = {"primary_ip4": {"address": "10.0.0.1/32"}}
        assert extract_ip(device) == "10.0.0.1"

    def test_no_primary_ip4(self):
        assert extract_ip({}) is None
        assert extract_ip({"primary_ip4": None}) is None

    def test_empty_address(self):
        assert extract_ip({"primary_ip4": {"address": ""}}) is None
