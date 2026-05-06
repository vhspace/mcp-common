"""Tests for error handling in MCP tools."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from netbox_mcp.server import (
    _ensure_client,
    _extract_ip_address,
    _netbox_api_call,
    netbox_get_objects,
)


class TestEnsureClient:
    def test_raises_when_client_is_none(self):
        with patch("netbox_mcp.server.netbox", None):
            with pytest.raises(RuntimeError, match="not initialized"):
                _ensure_client()

    def test_returns_client_when_initialized(self):
        mock_client = MagicMock()
        with patch("netbox_mcp.server.netbox", mock_client):
            assert _ensure_client() is mock_client


class TestExtractIpAddress:
    def test_extracts_ipv4_from_netbox_object(self):
        ip_obj = {"id": 1, "address": "10.20.30.40/24", "family": 4}
        assert _extract_ip_address(ip_obj) == "10.20.30.40"

    def test_extracts_ipv6(self):
        ip_obj = {"id": 2, "address": "2001:db8::1/64", "family": 6}
        assert _extract_ip_address(ip_obj) == "2001:db8::1"

    def test_returns_none_for_none(self):
        assert _extract_ip_address(None) is None

    def test_returns_none_for_empty_dict(self):
        assert _extract_ip_address({}) is None

    def test_returns_none_for_empty_address(self):
        assert _extract_ip_address({"address": ""}) is None

    def test_handles_address_without_cidr(self):
        assert _extract_ip_address({"address": "10.0.0.1"}) == "10.0.0.1"


class TestNetboxApiCall:
    def test_wraps_http_error_with_status(self):
        response = MagicMock()
        response.status_code = 403
        response.json.return_value = {"detail": "Authentication credentials were not provided."}
        exc = requests.HTTPError(response=response)

        def failing_fn():
            raise exc

        with pytest.raises(ValueError, match="HTTP 403"):
            _netbox_api_call(failing_fn)

    def test_wraps_connection_error(self):
        def failing_fn():
            raise requests.ConnectionError("Connection refused")

        with pytest.raises(ValueError, match="Could not connect"):
            _netbox_api_call(failing_fn)

    def test_wraps_timeout_error(self):
        def failing_fn():
            raise requests.Timeout("Request timed out")

        with pytest.raises(ValueError, match="timed out"):
            _netbox_api_call(failing_fn)

    def test_passes_through_on_success(self):
        def ok_fn(x, y=None):
            return {"result": x, "y": y}

        assert _netbox_api_call(ok_fn, "a", y="b") == {"result": "a", "y": "b"}

    def test_http_error_without_json_body(self):
        response = MagicMock()
        response.status_code = 500
        response.json.side_effect = ValueError("No JSON")
        response.text = "Internal Server Error"
        exc = requests.HTTPError(response=response)

        def failing_fn():
            raise exc

        with pytest.raises(ValueError, match=r"HTTP 500.*Internal Server Error"):
            _netbox_api_call(failing_fn)


class TestToolErrorHandling:
    @patch("netbox_mcp.server.netbox")
    def test_get_objects_wraps_http_errors(self, mock_netbox):
        """HTTP errors should surface as ToolError (via remediation wrapper) with HTTP status."""
        from fastmcp.exceptions import ToolError

        response = MagicMock()
        response.status_code = 404
        response.json.return_value = {"detail": "Not found."}
        mock_netbox.get.side_effect = requests.HTTPError(response=response)

        with pytest.raises(ToolError, match="HTTP 404"):
            netbox_get_objects(object_type="dcim.device", filters={})
