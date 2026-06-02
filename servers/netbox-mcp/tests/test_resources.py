"""Tests for MCP resources and resource templates."""

import json
from unittest.mock import patch

from netbox_mcp.server import (
    get_device_resource,
    get_ip_resource,
    get_rack_resource,
    get_site_resource,
    health,
    list_object_types,
    server_info,
)

# ============================================================================
# Static Resources
# ============================================================================


def test_list_object_types_returns_valid_json():
    result = list_object_types()
    data = json.loads(result)
    assert isinstance(data, dict)
    assert "dcim.device" in data
    assert "endpoint" in data["dcim.device"]


def test_server_info_returns_version_and_tools():
    result = server_info()
    data = json.loads(result)
    assert "version" in data
    assert "tools" in data
    assert "netbox_lookup_device" in data["tools"]
    assert data["supported_object_types_count"] > 0


@patch("netbox_mcp.server.netbox")
def test_health_resource_healthy(mock_netbox):
    mock_netbox.get.return_value = {"netbox-status": "ok"}
    result = health()
    data = json.loads(result)
    assert data["status"] == "healthy"
    assert data["checks"]["netbox_api"] is True


@patch("netbox_mcp.server.netbox")
def test_health_resource_degraded(mock_netbox):
    mock_netbox.get.side_effect = Exception("connection refused")
    result = health()
    data = json.loads(result)
    assert data["status"] == "degraded"
    assert data["checks"]["netbox_api"] is False


def test_health_resource_no_client():
    with patch("netbox_mcp.server.netbox", None):
        result = health()
        data = json.loads(result)
        assert data["status"] == "healthy"
        assert data["checks"] == {}


# ============================================================================
# Resource Templates
# ============================================================================


@patch("netbox_mcp.server.netbox")
def test_device_template_returns_enriched_data(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [
            {
                "id": 1,
                "name": "gpu-01",
                "primary_ip4": {"address": "10.0.0.1/24"},
                "primary_ip6": None,
                "oob_ip": {"address": "192.168.1.1/24"},
            }
        ],
    }
    result = json.loads(get_device_resource("gpu-01"))
    assert result["count"] == 1
    assert result["results"][0]["oob_ip_address"] == "192.168.1.1"
    assert result["results"][0]["primary_ip4_address"] == "10.0.0.1"


@patch("netbox_mcp.server.netbox")
def test_device_template_empty_result(mock_netbox):
    mock_netbox.get.return_value = {"count": 0, "results": []}
    result = json.loads(get_device_resource("nonexistent"))
    assert result["count"] == 0
    assert result["results"] == []


@patch("netbox_mcp.server.netbox")
def test_site_template_found(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "name": "DC1", "slug": "dc1", "status": {"value": "active"}}],
    }
    result = json.loads(get_site_resource("dc1"))
    assert result["name"] == "DC1"
    assert result["slug"] == "dc1"


@patch("netbox_mcp.server.netbox")
def test_site_template_not_found(mock_netbox):
    mock_netbox.get.return_value = {"count": 0, "results": []}
    result = json.loads(get_site_resource("nonexistent"))
    assert "error" in result


@patch("netbox_mcp.server.netbox")
def test_ip_template_returns_matches(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "address": "10.0.0.1/24", "status": {"value": "active"}}],
    }
    result = json.loads(get_ip_resource("10.0.0.1"))
    assert result["count"] == 1
    assert result["query"] == "10.0.0.1"


@patch("netbox_mcp.server.netbox")
def test_rack_template_returns_matches(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "name": "Rack-A1", "site": {"slug": "dc1"}}],
    }
    result = json.loads(get_rack_resource("dc1", "Rack-A1"))
    assert result["count"] == 1
    assert result["query"] == "dc1/Rack-A1"
