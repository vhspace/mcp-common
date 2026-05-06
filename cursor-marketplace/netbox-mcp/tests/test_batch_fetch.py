"""Tests for netbox_get_objects_by_ids batch fetch tool."""

from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

from netbox_mcp.server import _serialize_filters, netbox_get_objects_by_ids


class TestSerializeFilters:
    def test_in_suffix_stripped_for_list_values(self):
        result = _serialize_filters({"id__in": [1, 2, 3]})
        assert result == {"id": [1, 2, 3]}

    def test_scalar_values_passed_through(self):
        result = _serialize_filters({"name": "router", "limit": 10})
        assert result == {"name": "router", "limit": 10}

    def test_mixed_list_and_scalar(self):
        result = _serialize_filters({"id__in": [10, 20], "status": "active"})
        assert result == {"id": [10, 20], "status": "active"}

    def test_empty_list_strips_suffix(self):
        result = _serialize_filters({"id__in": []})
        assert result == {"id": []}

    def test_scalar_in_suffix_not_stripped(self):
        result = _serialize_filters({"name__in": "foo"})
        assert result == {"name__in": "foo"}

    def test_non_in_list_kept_as_is(self):
        result = _serialize_filters({"tag": ["web", "prod"]})
        assert result == {"tag": ["web", "prod"]}

    def test_empty_dict(self):
        assert _serialize_filters({}) == {}


class TestBatchFetchByIds:
    @patch("netbox_mcp.server.netbox")
    def test_returns_empty_for_empty_ids(self, mock_netbox):
        result = netbox_get_objects_by_ids(object_type="dcim.device", ids=[])
        assert result == {"count": 0, "results": []}
        mock_netbox.get.assert_not_called()

    @patch("netbox_mcp.server.netbox")
    def test_builds_id_param_as_list(self, mock_netbox):
        mock_netbox.get.return_value = {
            "count": 2,
            "results": [
                {"id": 1, "name": "device-a"},
                {"id": 2, "name": "device-b"},
            ],
        }

        result = netbox_get_objects_by_ids(
            object_type="dcim.device", ids=[1, 2]
        )

        mock_netbox.get.assert_called_once()
        call_kwargs = mock_netbox.get.call_args[1]
        assert call_kwargs["params"]["id"] == [1, 2]
        assert call_kwargs["params"]["limit"] == 2
        assert result["count"] == 2
        assert len(result["results"]) == 2

    @patch("netbox_mcp.server.netbox")
    def test_forwards_fields_param(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 1, "results": [{"id": 5, "name": "sw1"}]}

        netbox_get_objects_by_ids(
            object_type="dcim.device",
            ids=[5],
            fields=["id", "name"],
        )

        params = mock_netbox.get.call_args[1]["params"]
        assert params["fields"] == "id,name"

    @patch("netbox_mcp.server.netbox")
    def test_forwards_brief_param(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 1, "results": [{"id": 5}]}

        netbox_get_objects_by_ids(
            object_type="dcim.device", ids=[5], brief=True
        )

        params = mock_netbox.get.call_args[1]["params"]
        assert params["brief"] == "1"

    @patch("netbox_mcp.server.netbox")
    def test_brief_false_omits_param(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 1, "results": [{"id": 5}]}

        netbox_get_objects_by_ids(
            object_type="dcim.device", ids=[5], brief=False
        )

        params = mock_netbox.get.call_args[1]["params"]
        assert "brief" not in params

    @patch("netbox_mcp.server.netbox")
    def test_deduplicates_ids(self, mock_netbox):
        mock_netbox.get.return_value = {"count": 1, "results": [{"id": 3}]}

        netbox_get_objects_by_ids(
            object_type="dcim.device", ids=[3, 3, 3]
        )

        params = mock_netbox.get.call_args[1]["params"]
        assert params["id"] == [3]
        assert params["limit"] == 1

    @patch("netbox_mcp.server.netbox")
    def test_paginates_large_id_lists(self, mock_netbox):
        """IDs exceeding 100 per page are fetched in chunks."""
        ids = list(range(1, 151))
        chunk1_results = [{"id": i} for i in range(1, 101)]
        chunk2_results = [{"id": i} for i in range(101, 151)]

        mock_netbox.get.side_effect = [
            {"count": 100, "results": chunk1_results},
            {"count": 50, "results": chunk2_results},
        ]

        result = netbox_get_objects_by_ids(
            object_type="dcim.device", ids=ids
        )

        assert mock_netbox.get.call_count == 2
        assert result["count"] == 150
        assert len(result["results"]) == 150

        first_call_params = mock_netbox.get.call_args_list[0][1]["params"]
        assert first_call_params["limit"] == 100

        second_call_params = mock_netbox.get.call_args_list[1][1]["params"]
        assert second_call_params["limit"] == 50

    def test_rejects_invalid_object_type(self):
        with pytest.raises(ToolError, match="Invalid object_type"):
            netbox_get_objects_by_ids(
                object_type="invalid.type", ids=[1]
            )

    @patch("netbox_mcp.server.netbox")
    def test_works_with_cluster_type(self, mock_netbox):
        mock_netbox.get.return_value = {
            "count": 2,
            "results": [
                {"id": 10, "name": "cluster-a"},
                {"id": 20, "name": "cluster-b"},
            ],
        }

        result = netbox_get_objects_by_ids(
            object_type="virtualization.cluster",
            ids=[10, 20],
            fields=["id", "name"],
        )

        assert result["count"] == 2
