"""Tests for completion handler."""

from unittest.mock import patch

import pytest
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)

from netbox_mcp.server import handle_completion


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_completion_for_hostname(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 2,
        "results": [
            {"id": 1, "name": "gpu-node-01"},
            {"id": 2, "name": "gpu-node-02"},
        ],
    }

    result = await handle_completion(
        PromptReference(type="ref/prompt", name="investigate_device"),
        CompletionArgument(name="hostname", value="gpu"),
        None,
    )

    assert isinstance(result, Completion)
    assert result.values == ["gpu-node-01", "gpu-node-02"]
    mock_netbox.get.assert_called_once()
    call_params = mock_netbox.get.call_args[1]["params"]
    assert call_params["name__isw"] == "gpu"
    assert call_params["limit"] == 10


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_completion_for_site_slug(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "slug": "ori-tx", "name": "ORI-TX"}],
    }

    result = await handle_completion(
        ResourceTemplateReference(type="ref/resource", uri="netbox://site/{slug}"),
        CompletionArgument(name="slug", value="ori"),
        None,
    )

    assert isinstance(result, Completion)
    assert result.values == ["ori-tx"]


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_completion_for_ip_address(mock_netbox):
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "address": "10.0.0.1/24"}],
    }

    result = await handle_completion(
        ResourceTemplateReference(type="ref/resource", uri="netbox://ip/{address}"),
        CompletionArgument(name="address", value="10.0"),
        None,
    )

    assert isinstance(result, Completion)
    assert result.values == ["10.0.0.1/24"]


@pytest.mark.anyio
async def test_completion_returns_none_for_unknown_argument():
    result = await handle_completion(
        PromptReference(type="ref/prompt", name="some_prompt"),
        CompletionArgument(name="unknown_arg", value="test"),
        None,
    )
    assert result is None


@pytest.mark.anyio
async def test_completion_returns_none_for_empty_value():
    result = await handle_completion(
        PromptReference(type="ref/prompt", name="investigate_device"),
        CompletionArgument(name="hostname", value=""),
        None,
    )
    assert result is None


@pytest.mark.anyio
async def test_completion_returns_none_when_no_client():
    with patch("netbox_mcp.server.netbox", None):
        result = await handle_completion(
            PromptReference(type="ref/prompt", name="investigate_device"),
            CompletionArgument(name="hostname", value="gpu"),
            None,
        )
    assert result is None


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_completion_handles_api_error(mock_netbox):
    mock_netbox.get.side_effect = Exception("connection refused")

    result = await handle_completion(
        PromptReference(type="ref/prompt", name="investigate_device"),
        CompletionArgument(name="hostname", value="gpu"),
        None,
    )
    assert result is None


@pytest.mark.anyio
@patch("netbox_mcp.server.netbox")
async def test_completion_with_context(mock_netbox):
    """Context (previous arguments) is accepted but doesn't change behavior."""
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [{"id": 1, "name": "Rack-A1"}],
    }

    result = await handle_completion(
        ResourceTemplateReference(type="ref/resource", uri="netbox://rack/{site_slug}/{rack_name}"),
        CompletionArgument(name="rack_name", value="Rack"),
        CompletionContext(arguments={"site_slug": "dc1"}),
    )

    assert isinstance(result, Completion)
    assert result.values == ["Rack-A1"]
