"""Tests for the NetBoxRestClient."""

import pytest
from mcp_common.credential_chain import CredentialChain, ResolvedAuth, StaticResolver
from requests import PreparedRequest

from netbox_mcp import __version__
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

    def test_uses_resolved_auth(self, client):
        assert isinstance(client.session.auth, ResolvedAuth)
        req = PreparedRequest()
        req.headers = {}
        req = client.session.auth(req)
        assert req.headers["Authorization"] == "Token test-token-123"

    def test_no_static_auth_header(self, client):
        assert "Authorization" not in client.session.headers

    def test_sets_json_headers(self, client):
        assert client.session.headers["Content-Type"] == "application/json"
        assert client.session.headers["Accept"] == "application/json"

    def test_sets_explicit_user_agent(self, client):
        """An explicit netbox-mcp/<version> UA must be set so the Cloudflare WAF
        in front of i.together.ai (CF Error 1010) does not 403 us; the bare
        urllib/requests default is the banned/fragile case we avoid."""
        ua = client.session.headers["User-Agent"]
        assert ua == f"netbox-mcp/{__version__}"
        assert ua.startswith("netbox-mcp/")
        assert not ua.lower().startswith("python-requests")
        assert "urllib" not in ua.lower()

    def test_stores_verify_ssl(self, client):
        assert client.verify_ssl is True

    def test_verify_ssl_false(self):
        c = NetBoxRestClient(url="https://nb.test", token="t", verify_ssl=False)
        assert c.verify_ssl is False

    def test_accepts_credential_chain(self):
        chain = CredentialChain([StaticResolver("chain-token")], name="test")
        c = NetBoxRestClient(url="https://nb.test", token=chain)
        assert c.token == "chain-token"
        assert isinstance(c.session.auth, ResolvedAuth)

    def test_token_property_returns_resolved_value(self, client):
        assert client.token == "test-token-123"


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
