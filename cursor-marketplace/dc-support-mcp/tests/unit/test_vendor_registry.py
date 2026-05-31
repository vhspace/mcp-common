"""Unit tests for VendorRegistry."""

from unittest.mock import patch

import pytest

from dc_support_mcp.validation import ValidationError
from dc_support_mcp.vendor_handler import VendorHandler
from dc_support_mcp.vendors.vendor_registry import VendorRegistry


class StubHandler(VendorHandler):
    """Minimal handler for testing the registry."""

    def __init__(self, email: str, password: str, verbose: bool = False):
        self.email = email
        self.password = password
        self.verbose = verbose
        self._closed = False

    def authenticate(self) -> bool:
        return True

    def get_ticket(self, ticket_id):
        return {"id": ticket_id}

    def list_tickets(self, status=None, limit=10):
        return []

    def close(self):
        self._closed = True


@pytest.mark.unit
class TestVendorRegistry:
    def test_register_and_list(self):
        registry = VendorRegistry()
        registry.register("test", StubHandler)
        assert "test" in registry.list_vendors()

    def test_register_case_insensitive(self):
        registry = VendorRegistry()
        registry.register("TEST", StubHandler)
        assert "test" in registry.list_vendors()
        assert registry.is_vendor_registered("TEST")
        assert registry.is_vendor_registered("test")

    def test_register_non_vendor_handler_raises(self):
        registry = VendorRegistry()
        with pytest.raises(ValueError, match="must inherit"):
            registry.register("bad", dict)  # type: ignore[arg-type]

    @patch.dict("os.environ", {"TEST_PORTAL_USERNAME": "u", "TEST_PORTAL_PASSWORD": "p"})
    def test_get_handler_creates_instance(self):
        registry = VendorRegistry()
        registry.register("test", StubHandler)
        handler = registry.get_handler("test")
        assert isinstance(handler, StubHandler)
        assert handler.email == "u"

    @patch.dict("os.environ", {"TEST_PORTAL_USERNAME": "u", "TEST_PORTAL_PASSWORD": "p"})
    def test_get_handler_caches_instance(self):
        registry = VendorRegistry()
        registry.register("test", StubHandler)
        h1 = registry.get_handler("test")
        h2 = registry.get_handler("test")
        assert h1 is h2

    def test_get_handler_unknown_vendor_raises(self):
        registry = VendorRegistry()
        with pytest.raises(ValidationError, match="not registered"):
            registry.get_handler("unknown")

    def test_get_handler_missing_creds_raises(self):
        registry = VendorRegistry()
        registry.register("nocreds", StubHandler)
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValidationError, match="Missing credentials"):
                registry.get_handler("nocreds")

    @patch.dict("os.environ", {"TEST_PORTAL_USERNAME": "u", "TEST_PORTAL_PASSWORD": "p"})
    def test_clear_cache_calls_close(self):
        registry = VendorRegistry()
        registry.register("test", StubHandler)
        handler = registry.get_handler("test")
        assert not handler._closed

        registry.clear_cache("test")
        assert handler._closed

    @patch.dict("os.environ", {"TEST_PORTAL_USERNAME": "u", "TEST_PORTAL_PASSWORD": "p"})
    def test_clear_cache_all(self):
        registry = VendorRegistry()
        registry.register("test", StubHandler)
        handler = registry.get_handler("test")

        registry.clear_cache()
        assert handler._closed
        # Re-creating should give a new instance
        h2 = registry.get_handler("test")
        assert h2 is not handler

    def test_is_vendor_registered(self):
        registry = VendorRegistry()
        assert not registry.is_vendor_registered("test")
        registry.register("test", StubHandler)
        assert registry.is_vendor_registered("test")
