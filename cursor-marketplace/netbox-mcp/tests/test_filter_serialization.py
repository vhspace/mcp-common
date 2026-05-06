"""Tests for list-valued filter serialization (issue #71).

NetBox 4.x silently ignores ``__in`` suffixes.  The correct way to
filter by multiple values is repeated keys on the base field name
(``?id=1&id=2&id=3``), which ``requests`` produces naturally for list
params.  ``_serialize_filters`` strips ``__in`` so agents can write
``{"id__in": [1, 2, 3]}`` and get the right query.
"""

import pytest

from netbox_mcp.server import _serialize_filters


class TestSerializeFilters:
    """Unit tests for _serialize_filters."""

    def test_id_in_strips_suffix(self):
        assert _serialize_filters({"id__in": [1, 2, 3]}) == {"id": [1, 2, 3]}

    def test_cluster_id_in_strips_suffix(self):
        assert _serialize_filters({"cluster_id__in": [631, 164, 122]}) == {
            "cluster_id": [631, 164, 122]
        }

    def test_scalar_values_pass_through(self):
        filters = {"site_id": 1, "name": "router", "status": "active"}
        assert _serialize_filters(filters) == filters

    def test_mixed_list_and_scalar(self):
        result = _serialize_filters({"site_id": 5, "id__in": [10, 20], "name": "sw"})
        assert result == {"site_id": 5, "id": [10, 20], "name": "sw"}

    def test_empty_list_strips_suffix(self):
        assert _serialize_filters({"id__in": []}) == {"id": []}

    def test_single_element_list(self):
        assert _serialize_filters({"id__in": [42]}) == {"id": [42]}

    def test_non_in_list_values_kept_as_is(self):
        """Lists on non-__in keys pass through unchanged (requests handles them)."""
        assert _serialize_filters({"tag": ["web", "prod"]}) == {"tag": ["web", "prod"]}

    def test_scalar_in_suffix_not_stripped(self):
        """Scalar values with __in suffix are kept as-is (not a list lookup)."""
        assert _serialize_filters({"name__in": "foo"}) == {"name__in": "foo"}

    def test_empty_filters_returns_empty(self):
        assert _serialize_filters({}) == {}

    def test_does_not_mutate_original(self):
        original = {"id__in": [1, 2, 3], "name": "test"}
        _serialize_filters(original)
        assert original == {"id__in": [1, 2, 3], "name": "test"}
