"""
End-to-end tests for Ori Industries portal.
These tests make real API calls (READ-ONLY operations only).

Run with: pytest -m e2e
Requires: ORI_PORTAL_USERNAME and ORI_PORTAL_PASSWORD environment variables
"""

from unittest.mock import patch

import pytest

from dc_support_mcp.vendors.ori import OriVendorHandler as OriSessionManager


@pytest.mark.e2e
@pytest.mark.slow
class TestOriPortalE2E:
    """End-to-end tests with real Ori portal (read-only)."""

    def test_authentication(self, real_credentials):
        """Test real authentication flow."""
        manager = OriSessionManager(
            email=real_credentials["email"],
            password=real_credentials["password"],
            use_cached_cookies=False,  # Force fresh auth
        )

        # Verify session has cookies
        assert len(manager.session.cookies) > 0

        # Should have the critical session cookie
        cookie_names = [cookie.name for cookie in manager.session.cookies]
        assert (
            "customer.account.session.token" in cookie_names
            or "atlassian.xsrf.token" in cookie_names
        )

    def test_get_known_ticket(self, real_credentials):
        """Test fetching a known ticket (SUPP-1556)."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        # Fetch the test ticket
        ticket = manager.get_ticket("SUPP-1556")

        # Verify basic structure
        assert ticket is not None, "Failed to fetch ticket"
        assert ticket["id"] == "SUPP-1556"
        assert ticket["summary"] is not None
        assert ticket["status"] is not None
        assert ticket["url"] is not None

        # Verify it has the expected content
        assert "AS40475" in ticket["summary"]
        assert ticket["reporter"] == "tsparks@together.ai"

        # Verify comments exist
        assert "comments" in ticket
        assert len(ticket["comments"]) > 0

        # Verify comment structure
        first_comment = ticket["comments"][0]
        assert "author" in first_comment
        assert "date" in first_comment
        assert "comment" in first_comment

    def test_get_nonexistent_ticket(self, real_credentials):
        """Test fetching a ticket that doesn't exist."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        # Try to fetch a ticket that likely doesn't exist
        ticket = manager.get_ticket("SUPP-999999")

        # Should return None or empty data
        assert ticket is None or ticket.get("id") is None

    def test_cookie_caching(self, real_credentials, tmp_path):
        """Test that cookies are cached and reused."""
        cache_file = tmp_path / "test_cache.pkl"

        with patch.object(OriSessionManager, "COOKIE_FILE", cache_file):
            # First manager - should authenticate
            manager1 = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=False,
            )

            # Fetch a ticket to ensure session works
            ticket1 = manager1.get_ticket("SUPP-1556")
            assert ticket1 is not None

            # Verify cache file was created
            assert cache_file.exists()

            # Second manager - should use cached cookies
            manager2 = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=True,
            )

            # Should be able to fetch without re-authenticating
            ticket2 = manager2.get_ticket("SUPP-1556")
            assert ticket2 is not None
            assert ticket2["id"] == ticket1["id"]

    def test_session_refresh_on_expiry(self, real_credentials, tmp_path):
        """Test that expired sessions are automatically refreshed."""
        cache_file = tmp_path / "expired_cache.pkl"

        # Create an expired cache
        import pickle
        from datetime import datetime, timedelta

        expired_data = {
            "cookies": [
                {"name": "fake-cookie", "value": "expired", "domain": ".atlassian.net", "path": "/"}
            ],
            "timestamp": datetime.now() - timedelta(hours=5),
        }

        with open(cache_file, "wb") as f:
            pickle.dump(expired_data, f)

        with patch.object(OriSessionManager, "COOKIE_FILE", cache_file):
            # Should detect expired cache and re-authenticate
            manager = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=True,
            )

            # Should still work after re-auth
            ticket = manager.get_ticket("SUPP-1556")
            assert ticket is not None

    def test_multiple_tickets(self, real_credentials):
        """Test fetching multiple tickets."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        # Fetch the known ticket
        ticket1 = manager.get_ticket("SUPP-1556")
        assert ticket1 is not None

        # Fetch it again (should use cache)
        ticket2 = manager.get_ticket("SUPP-1556")
        assert ticket2 is not None

        # Should have same data
        assert ticket1["id"] == ticket2["id"]
        assert ticket1["summary"] == ticket2["summary"]

    def test_ticket_data_completeness(self, real_credentials):
        """Test that all expected fields are present in ticket data."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        ticket = manager.get_ticket("SUPP-1556")
        assert ticket is not None

        # Verify all expected fields
        required_fields = [
            "id",
            "summary",
            "status",
            "reporter",
            "assignee",
            "created",
            "organisations",
            "request_type",
            "comments",
            "url",
        ]

        for field in required_fields:
            assert field in ticket, f"Missing required field: {field}"

        # Verify URL format
        assert ticket["url"].startswith("https://oriindustries.atlassian.net")
        assert "SUPP-1556" in ticket["url"]

    def test_comment_structure(self, real_credentials):
        """Test that comments have correct structure."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        ticket = manager.get_ticket("SUPP-1556")
        assert ticket is not None
        assert len(ticket["comments"]) > 0

        # Check first comment structure
        comment = ticket["comments"][0]
        assert "author" in comment
        assert "date" in comment
        assert "comment" in comment
        assert "type" in comment

        # Verify comment types are valid
        valid_types = ["requester-comment", "worker-comment"]
        assert comment["type"] in valid_types

    def test_performance_cached_vs_uncached(self, real_credentials, tmp_path):
        """Test performance difference between cached and uncached requests."""
        import time

        cache_file = tmp_path / "perf_cache.pkl"

        with patch.object(OriSessionManager, "COOKIE_FILE", cache_file):
            # First request (uncached) - will be slower
            start1 = time.time()
            manager1 = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=False,
            )
            ticket1 = manager1.get_ticket("SUPP-1556")
            time1 = time.time() - start1

            assert ticket1 is not None

            # Second request (cached) - should be faster
            start2 = time.time()
            manager2 = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=True,
            )
            ticket2 = manager2.get_ticket("SUPP-1556")
            time2 = time.time() - start2

            assert ticket2 is not None

            # Cached should be significantly faster
            # (at least 5x faster, typically 10-15x)
            print(f"\nUncached: {time1:.2f}s, Cached: {time2:.2f}s, Speedup: {time1 / time2:.1f}x")
            assert time2 < time1 / 3, "Cached request should be at least 3x faster"


@pytest.mark.e2e
class TestOriPortalReadOnly:
    """Additional read-only E2E tests."""

    def test_url_construction(self, real_credentials):
        """Test that URLs are correctly constructed."""
        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        ticket = manager.get_ticket("SUPP-1556")
        assert ticket is not None

        expected_url = "https://oriindustries.atlassian.net/servicedesk/customer/portal/3/SUPP-1556"
        assert ticket["url"] == expected_url

    def test_error_handling_network_timeout(self, real_credentials, tmp_path):
        """Test handling of network timeouts."""
        with patch.object(OriSessionManager, "COOKIE_FILE", tmp_path / "cookies.pkl"):
            manager = OriSessionManager(
                email=real_credentials["email"],
                password=real_credentials["password"],
                use_cached_cookies=False,
            )

            # Mock a timeout
            with patch.object(manager.session, "post") as mock_post:
                mock_post.side_effect = Exception("Connection timeout")

                result = manager._make_api_request({"test": "data"})
                assert result is None

    def test_vendor_handler_compliance(self, real_credentials):
        """Test that OriSessionManager properly implements VendorHandler."""

        manager = OriSessionManager(
            email=real_credentials["email"], password=real_credentials["password"]
        )

        # Test authenticate method
        assert hasattr(manager, "authenticate")
        assert callable(manager.authenticate)

        # Test get_ticket method
        assert hasattr(manager, "get_ticket")
        ticket = manager.get_ticket("SUPP-1556")
        assert ticket is not None

        # Test list_tickets method
        assert hasattr(manager, "list_tickets")
        tickets = manager.list_tickets(status="open", limit=5)
        assert isinstance(tickets, list)
