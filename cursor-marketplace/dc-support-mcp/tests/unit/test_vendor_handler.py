"""
Unit tests for VendorHandler base class.
"""

import pytest

from dc_support_mcp.vendor_handler import VendorHandler


@pytest.mark.unit
class TestVendorHandler:
    """Test VendorHandler abstract base class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that VendorHandler cannot be instantiated directly."""
        with pytest.raises(TypeError):
            VendorHandler()

    def test_subclass_must_implement_methods(self):
        """Test that subclasses must implement abstract methods."""

        # Create incomplete subclass
        class IncompleteHandler(VendorHandler):
            pass

        # Should not be able to instantiate
        with pytest.raises(TypeError):
            IncompleteHandler()

    def test_complete_subclass_can_instantiate(self):
        """Test that complete subclass can be instantiated."""

        class CompleteHandler(VendorHandler):
            def authenticate(self) -> bool:
                return True

            def get_ticket(self, ticket_id: str):
                return {"id": ticket_id}

            def list_tickets(self, status=None, limit=10):
                return []

        # Should work
        handler = CompleteHandler()
        assert handler.authenticate() is True
        assert handler.get_ticket("TEST-123")["id"] == "TEST-123"
        assert handler.list_tickets() == []

    def test_normalize_ticket_basic(self):
        """Test normalize_ticket with basic data."""

        class TestHandler(VendorHandler):
            def authenticate(self):
                return True

            def get_ticket(self, ticket_id):
                return {}

            def list_tickets(self, status=None, limit=10):
                return []

        handler = TestHandler()

        raw_ticket = {
            "id": "TEST-123",
            "summary": "Test ticket",
            "status": "Open",
            "priority": "High",
            "reporter": "user@example.com",
            "assignee": "agent@example.com",
            "created": "2026-01-01",
            "updated": "2026-01-02",
            "url": "https://example.com/ticket/123",
            "comments": [],
        }

        normalized = handler.normalize_ticket(raw_ticket)

        assert normalized["id"] == "TEST-123"
        assert normalized["summary"] == "Test ticket"
        assert normalized["status"] == "Open"
        assert normalized["priority"] == "High"
        assert normalized["reporter"] == "user@example.com"
        assert normalized["assignee"] == "agent@example.com"

    def test_normalize_ticket_missing_fields(self):
        """Test normalize_ticket handles missing fields gracefully."""

        class TestHandler(VendorHandler):
            def authenticate(self):
                return True

            def get_ticket(self, ticket_id):
                return {}

            def list_tickets(self, status=None, limit=10):
                return []

        handler = TestHandler()

        # Minimal ticket data
        raw_ticket = {"id": "TEST-123"}

        normalized = handler.normalize_ticket(raw_ticket)

        # Should not raise, should have None values
        assert normalized["id"] == "TEST-123"
        assert normalized["summary"] is None
        assert normalized["comments"] == []

    def test_normalize_ticket_preserves_comments(self):
        """Test that comments are preserved during normalization."""

        class TestHandler(VendorHandler):
            def authenticate(self):
                return True

            def get_ticket(self, ticket_id):
                return {}

            def list_tickets(self, status=None, limit=10):
                return []

        handler = TestHandler()

        comments = [
            {"author": "user1", "text": "Comment 1"},
            {"author": "user2", "text": "Comment 2"},
        ]

        raw_ticket = {"id": "TEST-123", "comments": comments}

        normalized = handler.normalize_ticket(raw_ticket)

        assert len(normalized["comments"]) == 2
        assert normalized["comments"][0]["author"] == "user1"
        assert normalized["comments"][1]["author"] == "user2"
