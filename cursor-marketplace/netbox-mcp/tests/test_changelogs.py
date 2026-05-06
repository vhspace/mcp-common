"""Tests for netbox_get_changelogs tool."""

import inspect
from unittest.mock import patch

from netbox_mcp.server import netbox_get_changelogs


def test_changelogs_has_pagination_parameters():
    """netbox_get_changelogs should have limit and offset parameters."""
    sig = inspect.signature(netbox_get_changelogs)

    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert sig.parameters["limit"].default == 20
    assert sig.parameters["offset"].default == 0


def test_changelogs_has_fields_parameter():
    """netbox_get_changelogs should support field projection."""
    sig = inspect.signature(netbox_get_changelogs)
    assert "fields" in sig.parameters


@patch("netbox_mcp.server.netbox")
def test_changelogs_passes_pagination_params(mock_netbox):
    """Should pass limit and offset to the NetBox API."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_get_changelogs(filters={}, limit=10, offset=5)

    call_args = mock_netbox.get.call_args
    params = call_args[1]["params"]
    assert params["limit"] == 10
    assert params["offset"] == 5


@patch("netbox_mcp.server.netbox")
def test_changelogs_passes_fields_param(mock_netbox):
    """Should pass fields parameter when specified."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_get_changelogs(filters={}, fields=["id", "action", "changed_object_repr"])

    call_args = mock_netbox.get.call_args
    params = call_args[1]["params"]
    assert params["fields"] == "id,action,changed_object_repr"


@patch("netbox_mcp.server.netbox")
def test_changelogs_passes_filters(mock_netbox):
    """Should forward user-provided filters to the API."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_get_changelogs(filters={"action": "update", "user": "admin"})

    call_args = mock_netbox.get.call_args
    params = call_args[1]["params"]
    assert params["action"] == "update"
    assert params["user"] == "admin"


@patch("netbox_mcp.server.netbox")
def test_changelogs_uses_correct_endpoint(mock_netbox):
    """Should query core/object-changes endpoint."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_get_changelogs(filters={})

    call_args = mock_netbox.get.call_args
    assert call_args[0][0] == "core/object-changes"
