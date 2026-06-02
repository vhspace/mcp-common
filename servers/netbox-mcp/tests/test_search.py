"""Tests for global search functionality (netbox_search_objects tool)."""

from unittest.mock import patch

import pytest
from pydantic import TypeAdapter, ValidationError

from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES
from netbox_mcp.server import DEFAULT_SEARCH_TYPES, netbox_search_objects

# ============================================================================
# Parameter Validation Tests
# ============================================================================


def test_limit_validation_rejects_invalid_values():
    """Limit must be between 1 and 100."""
    import inspect

    sig = inspect.signature(netbox_search_objects)
    limit_annotation = sig.parameters["limit"].annotation
    adapter = TypeAdapter(limit_annotation)

    with pytest.raises(ValidationError):
        adapter.validate_python(0)

    with pytest.raises(ValidationError):
        adapter.validate_python(101)

    adapter.validate_python(1)
    adapter.validate_python(100)


@pytest.mark.anyio
async def test_invalid_object_type_raises_error():
    """Invalid object type should raise ToolError (via remediation wrapper) with helpful message."""
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Invalid object_type"):
        await netbox_search_objects(query="test", object_types=["invalid_type_xyz"])


# ============================================================================
# Default Behavior Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_searches_default_types_when_none_specified(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    result = await netbox_search_objects(query="test")

    assert mock_netbox.get.call_count == len(DEFAULT_SEARCH_TYPES)
    assert isinstance(result, dict)
    assert len(result) == len(DEFAULT_SEARCH_TYPES)


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_custom_object_types_limits_search_scope(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    result = await netbox_search_objects(query="test", object_types=["dcim.device", "dcim.site"])

    assert mock_netbox.get.call_count == 2
    assert set(result.keys()) == {"dcim.device", "dcim.site"}


# ============================================================================
# Field Projection Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_field_projection_applied_to_queries(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    await netbox_search_objects(
        query="test", object_types=["dcim.device", "dcim.site"], fields=["id", "name"]
    )

    for call_args in mock_netbox.get.call_args_list:
        params = call_args[1]["params"]
        assert params["fields"] == "id,name"


# ============================================================================
# Result Structure Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_result_structure_with_empty_and_populated_results(mock_netbox):
    def mock_get_side_effect(endpoint, params):
        if "devices" in endpoint:
            return {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 1, "name": "device01"}],
            }
        return {"count": 0, "next": None, "previous": None, "results": []}

    mock_netbox.get.side_effect = mock_get_side_effect

    result = await netbox_search_objects(
        query="test", object_types=["dcim.device", "dcim.site", "dcim.rack"]
    )

    assert set(result.keys()) == {"dcim.device", "dcim.site", "dcim.rack"}
    assert result["dcim.device"] == [{"id": 1, "name": "device01"}]
    assert result["dcim.site"] == []
    assert result["dcim.rack"] == []


# ============================================================================
# Error Resilience Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_continues_searching_when_one_type_fails(mock_netbox):
    def mock_get_side_effect(endpoint, params):
        if "devices" in endpoint:
            raise Exception("API error")
        elif "sites" in endpoint:
            return {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 1, "name": "site01"}],
            }
        return {"count": 0, "next": None, "previous": None, "results": []}

    mock_netbox.get.side_effect = mock_get_side_effect

    result = await netbox_search_objects(query="test", object_types=["dcim.device", "dcim.site"])

    assert result["dcim.site"] == [{"id": 1, "name": "site01"}]
    assert result["dcim.device"] == []


# ============================================================================
# NetBox API Integration Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_api_parameters_passed_correctly(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    await netbox_search_objects(
        query="switch01", object_types=["dcim.device"], fields=["id"], limit=25
    )

    call_args = mock_netbox.get.call_args
    params = call_args[1]["params"]

    assert params["q"] == "switch01"
    assert params["limit"] == 25
    assert params["fields"] == "id"


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_uses_correct_api_endpoints(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    await netbox_search_objects(query="test", object_types=["dcim.device", "ipam.ipaddress"])

    called_endpoints = [call[0][0] for call in mock_netbox.get.call_args_list]
    assert NETBOX_OBJECT_TYPES["dcim.device"]["endpoint"] in called_endpoints
    assert NETBOX_OBJECT_TYPES["ipam.ipaddress"]["endpoint"] in called_endpoints


# ============================================================================
# Paginated Response Handling Tests
# ============================================================================


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_extracts_results_from_paginated_response(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 2,
        "next": None,
        "previous": None,
        "results": [
            {"id": 1, "name": "device01"},
            {"id": 2, "name": "device02"},
        ],
    }

    result = await netbox_search_objects(query="test", object_types=["dcim.device"])

    assert "dcim.device" in result
    assert isinstance(result["dcim.device"], list)
    assert result["dcim.device"] == [
        {"id": 1, "name": "device01"},
        {"id": 2, "name": "device02"},
    ]
