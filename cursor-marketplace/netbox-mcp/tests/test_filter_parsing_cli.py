"""Tests for CLI filter parsing (Issue #12).

Verifies that _parse_filter_string correctly handles:
- Multiple key=value pairs separated by commas (AND semantics)
- Commas within values preserved (NetBox OR semantics within a field)
- Mixed scenarios
"""

from netbox_mcp.cli import _apply_filters, _parse_filter_string


class TestParseFilterString:
    def test_single_filter(self):
        assert _parse_filter_string("site=ori-tx") == {"site": "ori-tx"}

    def test_two_filters_and_semantics(self):
        result = _parse_filter_string("site=ori-tx,cluster=cartesia5")
        assert result == {"site": "ori-tx", "cluster": "cartesia5"}

    def test_three_filters(self):
        result = _parse_filter_string("site=ori-tx,cluster=cartesia5,status=active")
        assert result == {"site": "ori-tx", "cluster": "cartesia5", "status": "active"}

    def test_comma_in_value_preserved_for_or(self):
        """NetBox uses commas in values for OR within a single field."""
        result = _parse_filter_string("status=active,planned")
        assert result == {"status": "active,planned"}

    def test_in_filter_with_comma_values(self):
        result = _parse_filter_string("name__in=foo,bar,baz")
        assert result == {"name__in": "foo,bar,baz"}

    def test_mixed_and_with_or_value(self):
        """AND between fields, OR within a field value."""
        result = _parse_filter_string("site=ori-tx,status=active,planned,cluster=cartesia5")
        assert result == {
            "site": "ori-tx",
            "status": "active,planned",
            "cluster": "cartesia5",
        }

    def test_lookup_suffix_filters(self):
        result = _parse_filter_string("name__ic=switch,site__isw=ori")
        assert result == {"name__ic": "switch", "site__isw": "ori"}

    def test_value_with_equals_sign(self):
        result = _parse_filter_string("tag=key=value")
        assert result == {"tag": "key=value"}

    def test_whitespace_stripped(self):
        result = _parse_filter_string(" site = ori-tx , cluster = cartesia5 ")
        assert result == {"site": "ori-tx", "cluster": "cartesia5"}

    def test_empty_string(self):
        assert _parse_filter_string("") == {}

    def test_numeric_values(self):
        result = _parse_filter_string("site_id=5,rack_id=10")
        assert result == {"site_id": "5", "rack_id": "10"}


class TestApplyFilters:
    def test_none_filters_noop(self):
        params: dict = {"limit": 10}
        _apply_filters(params, None)
        assert params == {"limit": 10}

    def test_empty_list_noop(self):
        params: dict = {"limit": 10}
        _apply_filters(params, [])
        assert params == {"limit": 10}

    def test_single_filter_string(self):
        params: dict = {"limit": 10}
        _apply_filters(params, ["site=ori-tx,cluster=cartesia5"])
        assert params == {"limit": 10, "site": "ori-tx", "cluster": "cartesia5"}

    def test_multiple_filter_strings(self):
        """Multiple --filter flags should all apply (AND)."""
        params: dict = {"limit": 10}
        _apply_filters(params, ["site=ori-tx", "cluster=cartesia5"])
        assert params == {"limit": 10, "site": "ori-tx", "cluster": "cartesia5"}

    def test_mixed_styles(self):
        """Comma-separated in one flag plus separate flags."""
        params: dict = {}
        _apply_filters(params, ["site=ori-tx,status=active", "cluster=cartesia5"])
        assert params == {"site": "ori-tx", "status": "active", "cluster": "cartesia5"}
