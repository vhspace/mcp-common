"""Tests for the helpers module."""

import pytest

from redfish_mcp.helpers import CurlCommandBuilder, ResponseBuilder, SystemFetcher


class TestResponseBuilder:
    def test_success(self):
        result = ResponseBuilder.success(data={"foo": "bar"}, extra="value")
        assert result["ok"] is True
        assert result["foo"] == "bar"
        assert result["extra"] == "value"

    def test_success_no_data(self):
        result = ResponseBuilder.success(key="value")
        assert result["ok"] is True
        assert result["key"] == "value"

    def test_error(self):
        result = ResponseBuilder.error("Something went wrong", detail="info")
        assert result["ok"] is False
        assert result["error"] == "Something went wrong"
        assert result["detail"] == "info"


class TestCurlCommandBuilder:
    def test_init_with_tls(self):
        curl = CurlCommandBuilder(verify_tls=True)
        assert curl.verify_tls is True

    def test_init_without_tls(self):
        curl = CurlCommandBuilder(verify_tls=False)
        assert curl.verify_tls is False

    def test_get_command(self):
        curl = CurlCommandBuilder(verify_tls=False)
        cmd = curl.get("/redfish/v1/Systems")
        assert "curl" in cmd
        assert "-sSk" in cmd  # insecure mode
        assert "$REDFISH_USER:$REDFISH_PASSWORD" in cmd
        assert "https://$REDFISH_IP/redfish/v1/Systems" in cmd

    def test_get_command_with_tls(self):
        curl = CurlCommandBuilder(verify_tls=True)
        cmd = curl.get("/redfish/v1/Systems")
        assert "curl" in cmd
        assert "-sSk" not in cmd  # secure mode
        assert "-sS" in cmd

    def test_patch_command(self):
        curl = CurlCommandBuilder(verify_tls=False)
        payload = '{"Boot": {"BootSourceOverrideTarget": "BiosSetup"}}'
        cmd = curl.patch("/redfish/v1/Systems/1", payload)
        assert "PATCH" in cmd
        assert "Content-Type: application/json" in cmd
        assert payload in cmd

    def test_post_command(self):
        curl = CurlCommandBuilder(verify_tls=False)
        payload = '{"ResetType": "ForceRestart"}'
        cmd = curl.post("/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", payload)
        assert "POST" in cmd
        assert payload in cmd

    def test_request_command_no_body(self):
        curl = CurlCommandBuilder(verify_tls=False)
        cmd = curl.request("DELETE", "/redfish/v1/Sessions/123")
        assert "DELETE" in cmd
        assert "/redfish/v1/Sessions/123" in cmd


class TestSystemFetcher:
    @pytest.mark.anyio
    async def test_get_system_caches_result(self):
        # Mock client and endpoint
        class MockClient:
            def __init__(self):
                self.call_count = 0

            def get_json_maybe(self, url):
                self.call_count += 1
                return ({"Id": "1", "Name": "System"}, None)

        class MockEndpoint:
            system_url = "https://test/redfish/v1/Systems/1"

        client = MockClient()
        endpoint = MockEndpoint()
        fetcher = SystemFetcher(client, endpoint)

        # First call
        system1, err1 = fetcher.get_system()
        assert system1 is not None
        assert err1 is None
        assert client.call_count == 1

        # Second call should use cache
        system2, err2 = fetcher.get_system()
        assert system2 is not None
        assert err2 is None
        assert client.call_count == 1  # Not incremented

    @pytest.mark.anyio
    async def test_get_system_or_error_response_success(self):
        class MockClient:
            def get_json_maybe(self, url):
                return ({"Id": "1"}, None)

        class MockEndpoint:
            system_url = "https://test/redfish/v1/Systems/1"

        client = MockClient()
        endpoint = MockEndpoint()
        fetcher = SystemFetcher(client, endpoint)

        system, err_response = fetcher.get_system_or_error_response("test-host")
        assert system is not None
        assert err_response is None

    @pytest.mark.anyio
    async def test_get_system_or_error_response_failure(self):
        class MockClient:
            def get_json_maybe(self, url):
                return (None, "404 Not Found")

        class MockEndpoint:
            system_url = "https://test/redfish/v1/Systems/1"

        client = MockClient()
        endpoint = MockEndpoint()
        fetcher = SystemFetcher(client, endpoint)

        system, err_response = fetcher.get_system_or_error_response("test-host")
        assert system is None
        assert err_response is not None
        assert err_response["ok"] is False
        assert "error" in err_response
        assert err_response["host"] == "test-host"
