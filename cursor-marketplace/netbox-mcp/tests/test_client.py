"""Tests for the NetBoxRestClient."""

import pytest

from netbox_mcp.netbox_client import NetBoxRestClient


@pytest.fixture
def client():
    return NetBoxRestClient(
        url="https://netbox.example.com/",
        token="test-token-123",
        verify_ssl=True,
    )


class TestClientInit:
    def test_strips_trailing_slash_from_url(self, client):
        assert client.base_url == "https://netbox.example.com"

    def test_builds_api_url(self, client):
        assert client.api_url == "https://netbox.example.com/api"

    def test_sets_auth_header(self, client):
        assert client.session.headers["Authorization"] == "Token test-token-123"

    def test_sets_json_headers(self, client):
        assert client.session.headers["Content-Type"] == "application/json"
        assert client.session.headers["Accept"] == "application/json"

    def test_stores_verify_ssl(self, client):
        assert client.verify_ssl is True

    def test_verify_ssl_false(self):
        c = NetBoxRestClient(url="https://nb.test", token="t", verify_ssl=False)
        assert c.verify_ssl is False


class TestBuildUrl:
    def test_list_endpoint(self, client):
        url = client._build_url("dcim/devices")
        assert url == "https://netbox.example.com/api/dcim/devices/"

    def test_detail_endpoint_with_id(self, client):
        url = client._build_url("dcim/devices", id=42)
        assert url == "https://netbox.example.com/api/dcim/devices/42/"

    def test_strips_leading_and_trailing_slashes(self, client):
        url = client._build_url("/dcim/devices/")
        assert url == "https://netbox.example.com/api/dcim/devices/"

    def test_handles_nested_endpoint(self, client):
        url = client._build_url("ipam/ip-addresses", id=100)
        assert url == "https://netbox.example.com/api/ipam/ip-addresses/100/"
