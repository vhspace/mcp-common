"""Tests for XID/SXid catalog lookup functions."""

from gpu_diag_mcp.xid_catalog import load_sxid_catalog, load_xid_catalog, sxid_lookup, xid_lookup


class TestXidLookup:
    def test_xid_94_found(self):
        result = xid_lookup(94)
        assert result["found"] is True
        assert result["code"] == 94
        assert "name" in result or "description" in result

    def test_xid_94_has_severity(self):
        result = xid_lookup(94)
        assert result["found"] is True
        assert any(k in result for k in ("severity", "category", "impact"))

    def test_xid_137_found(self):
        result = xid_lookup(137)
        assert result["found"] is True
        assert result["code"] == 137

    def test_xid_unknown_not_found(self):
        result = xid_lookup(9999)
        assert result["found"] is False
        assert result["code"] == 9999

    def test_xid_lookup_returns_dict(self):
        result = xid_lookup(94)
        assert isinstance(result, dict)


class TestSxidLookup:
    def test_sxid_12028_found(self):
        result = sxid_lookup(12028)
        assert result["found"] is True
        assert result["code"] == 12028

    def test_sxid_unknown_not_found(self):
        result = sxid_lookup(99999)
        assert result["found"] is False
        assert result["code"] == 99999


class TestLoadCatalogs:
    def test_load_xid_catalog_has_entries(self):
        catalog = load_xid_catalog()
        assert isinstance(catalog, dict)
        assert len(catalog) >= 20

    def test_load_xid_catalog_keys_are_ints(self):
        catalog = load_xid_catalog()
        for key in catalog:
            assert isinstance(key, int)

    def test_load_sxid_catalog_has_entries(self):
        catalog = load_sxid_catalog()
        assert isinstance(catalog, dict)
        assert len(catalog) >= 1

    def test_load_xid_catalog_caches(self):
        cat1 = load_xid_catalog()
        cat2 = load_xid_catalog()
        assert cat1 is cat2
