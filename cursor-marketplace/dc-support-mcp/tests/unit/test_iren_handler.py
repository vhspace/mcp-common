"""Unit tests for IrenVendorHandler (cookie management, KB cache, lifecycle, write ops)."""

import json
import pickle
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from dc_support_mcp.vendor_handler import VendorHandler
from dc_support_mcp.vendors.iren import (
    FRESHDESK_PRIORITY_MAP,
    FRESHDESK_STATUS_MAP,
    FRESHDESK_STATUS_NAMES,
    IrenVendorHandler,
)


def _make_iren_handler(mock_credentials, tmp_path, *, api_key: str = ""):
    """Create an IrenVendorHandler with no real browser."""
    handler = IrenVendorHandler(
        email=mock_credentials["email"],
        password=mock_credentials["password"],
        use_cached_cookies=False,
        verbose=False,
    )
    handler.cookie_file = tmp_path / "iren_cookies.pkl"
    handler.kb_cache_file = tmp_path / "iren_kb_cache.json"
    if api_key:
        handler._api_key = api_key
    return handler


@pytest.mark.unit
class TestIrenVendorHandler:
    def test_implements_vendor_handler(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        assert isinstance(handler, VendorHandler)

    def test_initialization(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        assert handler.email == mock_credentials["email"]
        assert handler.VENDOR_NAME == "iren"
        assert handler._authenticated is False

    def test_save_and_load_cookies(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        cookies = [
            {"name": "sess", "value": "abc123", "domain": ".iren.com", "path": "/"},
        ]
        handler._save_cookies(cookies)
        assert handler.cookie_file.exists()

        loaded = handler._load_cookies()
        assert loaded
        assert handler._cached_cookies == cookies

    def test_expired_cookies_not_loaded(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        expired = {
            "cookies": [{"name": "old", "value": "x", "domain": ".iren.com"}],
            "timestamp": datetime.now() - timedelta(hours=9),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(expired, f)

        assert not handler._load_cookies()

    def test_corrupted_cookies_handled(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        with open(handler.cookie_file, "wb") as f:
            f.write(b"not-a-pickle")

        assert not handler._load_cookies()

    def test_add_comment_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        result = handler.add_comment("123", "test comment")
        assert result is None

    def test_close_idempotent(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler.close()
        handler.close()  # should not raise

    def test_has_close_method(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        assert hasattr(handler, "close")
        assert callable(handler.close)


@pytest.mark.unit
class TestIrenKBCache:
    def test_save_and_load_kb_cache(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        articles = [
            {
                "id": "001",
                "title": "How to reset password",
                "url": "https://support.iren.com/articles/001",
                "category": None,
                "last_modified": None,
                "content": None,
                "attachments": [],
            }
        ]
        handler._save_kb_cache(articles)
        assert handler.kb_cache_file.exists()

        loaded = handler._load_kb_cache()
        assert loaded
        assert handler._kb_cache is not None
        assert len(handler._kb_cache["articles"]) == 1
        assert handler._kb_cache["articles"][0]["title"] == "How to reset password"

    def test_expired_kb_cache_not_loaded(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        cache_data = {
            "articles": [],
            "cached_at": (datetime.now() - timedelta(hours=25)).isoformat(),
            "last_modified": None,
        }
        with open(handler.kb_cache_file, "w") as f:
            json.dump(cache_data, f)

        assert not handler._load_kb_cache()

    def test_corrupted_kb_cache_handled(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        with open(handler.kb_cache_file, "w") as f:
            f.write("{invalid json")

        assert not handler._load_kb_cache()

    def test_search_kb_returns_empty_without_cache(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler._kb_cache = {
            "articles": [
                {
                    "id": "1",
                    "title": "Network Troubleshooting",
                    "url": "https://example.com/1",
                    "category": None,
                    "last_modified": None,
                    "content": None,
                    "attachments": [],
                },
                {
                    "id": "2",
                    "title": "Password Reset Guide",
                    "url": "https://example.com/2",
                    "category": None,
                    "last_modified": None,
                    "content": None,
                    "attachments": [],
                },
            ],
            "cached_at": datetime.now(),
            "last_modified": None,
        }
        handler._save_kb_cache(handler._kb_cache["articles"])

        results = handler.search_knowledge_base("network")
        assert len(results) == 1
        assert results[0]["title"] == "Network Troubleshooting"


@pytest.mark.unit
class TestExtractArticleId:
    """Tests for _extract_article_id (URL parsing and bare-ID handling)."""

    def test_bare_numeric_id(self):
        assert IrenVendorHandler._extract_article_id("12345") == "12345"

    def test_full_url(self):
        url = "https://support.iren.com/support/solutions/articles/73000682456-some-title"
        assert IrenVendorHandler._extract_article_id(url) == "73000682456"

    def test_relative_url(self):
        assert IrenVendorHandler._extract_article_id("/support/solutions/articles/99999") == "99999"

    def test_empty_string(self):
        assert IrenVendorHandler._extract_article_id("") == ""

    def test_no_digits(self):
        assert IrenVendorHandler._extract_article_id("abc") == ""


@pytest.mark.unit
class TestFetchArticleDirect:
    """Tests for _fetch_article_direct (REST API direct fetch)."""

    def test_rest_api_success(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        api_data = {
            "id": 42,
            "title": "How to reboot",
            "description": "<p>Step 1: press the button</p>",
            "folder_id": 10,
            "category_id": 5,
            "updated_at": "2025-06-01T12:00:00Z",
            "attachments": [
                {
                    "name": "guide.pdf",
                    "attachment_url": "https://support.iren.com/files/guide.pdf",
                    "content_type": "application/pdf",
                    "size": 1024,
                },
            ],
        }

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, api_data)
        ):
            article = handler._fetch_article_direct("42")

        assert article is not None
        assert article["id"] == "42"
        assert article["title"] == "How to reboot"
        assert "Step 1" in article["content"]
        assert article["category"] == "category/5/folder/10"
        assert len(article["attachments"]) == 1
        assert article["attachments"][0]["name"] == "guide.pdf"
        assert article["attachments"][0]["size"] == 1024

    def test_rest_api_failure_falls_back_to_browser(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(404)
        ), patch.object(
            handler, "_fetch_article_via_browser", return_value=None
        ) as mock_browser:
            result = handler._fetch_article_direct("999")

        assert result is None
        mock_browser.assert_called_once_with("999")

    def test_rest_api_none_response_falls_back(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(
            handler, "_freshdesk_request", return_value=None
        ), patch.object(
            handler, "_fetch_article_via_browser", return_value=None
        ) as mock_browser:
            handler._fetch_article_direct("123")

        mock_browser.assert_called_once_with("123")


@pytest.mark.unit
class TestGetKbArticleDirectFetch:
    """Tests for get_kb_article with direct fetch (bypasses cache)."""

    def test_direct_fetch_bypasses_cache(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        direct_article = {
            "id": "42",
            "title": "Direct Article",
            "url": "https://support.iren.com/support/solutions/articles/42",
            "category": None,
            "last_modified": None,
            "content": "Direct content",
            "attachments": [],
        }

        with patch.object(handler, "_fetch_article_direct", return_value=direct_article):
            result = handler.get_kb_article("42")

        assert result is not None
        assert result["title"] == "Direct Article"

    def test_accepts_full_url(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        direct_article = {
            "id": "73000682456",
            "title": "From URL",
            "url": "https://support.iren.com/support/solutions/articles/73000682456",
            "category": None,
            "last_modified": None,
            "content": "content",
            "attachments": [],
        }

        with patch.object(handler, "_fetch_article_direct", return_value=direct_article) as mock_direct:
            result = handler.get_kb_article(
                "https://support.iren.com/support/solutions/articles/73000682456-some-title"
            )

        assert result is not None
        mock_direct.assert_called_once_with("73000682456")

    def test_falls_back_to_cache_when_direct_fails(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler._kb_cache = {
            "articles": [
                {
                    "id": "100",
                    "title": "Cached Article",
                    "url": "https://support.iren.com/support/solutions/articles/100",
                    "category": None,
                    "last_modified": None,
                    "content": "cached content",
                    "attachments": [],
                },
            ],
            "cached_at": datetime.now(),
            "last_modified": None,
        }
        handler._save_kb_cache(handler._kb_cache["articles"])

        with patch.object(handler, "_fetch_article_direct", return_value=None):
            result = handler.get_kb_article("100")

        assert result is not None
        assert result["title"] == "Cached Article"

    def test_returns_none_for_empty_id(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        assert handler.get_kb_article("") is None
        assert handler.get_kb_article("abc") is None


@pytest.mark.unit
class TestFetchKbViaApi:
    """Tests for _fetch_kb_via_api (deep REST API discovery)."""

    def test_categories_folders_articles(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        categories_data = [{"id": 1, "name": "General"}]
        folders_data = [{"id": 10, "name": "Getting Started"}]
        articles_data = [
            {"id": 100, "title": "Welcome", "updated_at": "2025-01-01T00:00:00Z"},
        ]

        def side_effect(method, path, **kw):
            if "/solutions/categories" in path and "/folders" not in path:
                return _mock_response(200, categories_data)
            if "/folders" in path and "/articles" not in path:
                return _mock_response(200, folders_data)
            if "/articles" in path:
                if "page=1" in path:
                    return _mock_response(200, articles_data)
                return _mock_response(200, [])
            return _mock_response(404)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler._fetch_kb_via_api()

        assert len(result) == 1
        assert result[0]["id"] == "100"
        assert result[0]["title"] == "Welcome"
        assert result[0]["category"] == "General / Getting Started"

    def test_api_unavailable_returns_empty(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(handler, "_freshdesk_request", return_value=None):
            result = handler._fetch_kb_via_api()

        assert result == []

    def test_deduplicates_articles(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        categories_data = [{"id": 1, "name": "Cat"}]
        folders_data = [{"id": 10, "name": "Folder"}]
        articles_page = [
            {"id": 100, "title": "Dup Article"},
            {"id": 100, "title": "Dup Article"},
        ]

        def side_effect(method, path, **kw):
            if "/solutions/categories" in path and "/folders" not in path:
                return _mock_response(200, categories_data)
            if "/folders" in path and "/articles" not in path:
                return _mock_response(200, folders_data)
            if "/articles" in path:
                if "page=1" in path:
                    return _mock_response(200, articles_page)
                return _mock_response(200, [])
            return _mock_response(404)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler._fetch_kb_via_api()

        assert len(result) == 1


def _mock_response(status_code: int, json_data: dict | None = None, text: str = ""):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


@pytest.mark.unit
class TestIrenCreateTicket:
    """Tests for IrenVendorHandler.create_ticket (Freshdesk REST API)."""

    def test_create_ticket_success(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 42, "subject": "GPU down", "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ):
            result = handler.create_ticket(summary="GPU down", description="Node won't boot")

        assert result is not None
        assert result["id"] == "42"
        assert result["summary"] == "GPU down"
        assert result["status"] == "Open"
        assert "support.iren.com/support/tickets/42" in result["url"]

    def test_create_ticket_appends_cause(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 99, "subject": "test", "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ) as mock_req:
            handler.create_ticket(summary="test", description="details", cause="hardware fault")

        payload = mock_req.call_args.kwargs["json_body"]
        assert "hardware fault" in payload["description"]
        assert "Cause:" in payload["description"]

    def test_create_ticket_sanitizes_content(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 100, "subject": "test", "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ) as mock_req:
            handler.create_ticket(
                summary="SRE-1234 GPU issue on us-south-3a-r01-05",
                description="See https://linear.app/together-ai/issue/SRE-1234",
            )

        payload = mock_req.call_args.kwargs["json_body"]
        assert "SRE-1234" not in payload["subject"]
        assert "us-south-3a-r01-05" not in payload["subject"]
        assert "[internal ticket]" in payload["subject"]
        assert "linear.app" not in payload["description"]

    def test_create_ticket_priority_mapping(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 101, "status": 2}

        for label, expected_fd in [("P1", 4), ("P2", 3), ("P3", 2), ("P4", 1), ("P5", 1)]:
            with patch.object(
                handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
            ) as mock_req:
                handler.create_ticket(summary="x", description="y", priority=label)

            payload = mock_req.call_args.kwargs["json_body"]
            assert payload["priority"] == expected_fd, f"{label} should map to {expected_fd}"

    def test_create_ticket_sets_email_and_status(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 200, "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ) as mock_req:
            handler.create_ticket(summary="s", description="d")

        payload = mock_req.call_args.kwargs["json_body"]
        assert payload["email"] == mock_credentials["email"]
        assert payload["status"] == 2  # FRESHDESK_STATUS_OPEN

    def test_create_ticket_api_failure_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(
            handler,
            "_freshdesk_request",
            return_value=_mock_response(422, text="Validation failed"),
        ):
            result = handler.create_ticket(summary="x", description="y")

        assert result is None

    def test_create_ticket_network_error_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(handler, "_freshdesk_request", return_value=None):
            result = handler.create_ticket(summary="x", description="y")

        assert result is None

    def test_create_ticket_with_ticket_type(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 300, "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ) as mock_req:
            handler.create_ticket(summary="s", description="d", ticket_type="Incident")

        payload = mock_req.call_args.kwargs["json_body"]
        assert payload["type"] == "Incident"


@pytest.mark.unit
class TestIrenAddComment:
    """Tests for IrenVendorHandler.add_comment (Freshdesk REST API)."""

    def test_add_comment_success(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        note_resp = {"id": 555, "body": "Update here", "private": False}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, note_resp)
        ):
            result = handler.add_comment("42", "Update here", public=True)

        assert result is not None
        assert result["id"] == 555
        assert result["body"] == "Update here"

    def test_add_comment_private_note(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        note_resp = {"id": 556, "body": "Internal note", "private": True}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, note_resp)
        ) as mock_req:
            result = handler.add_comment("42", "Internal note", public=False)

        assert result is not None
        payload = mock_req.call_args.kwargs["json_body"]
        assert payload["private"] is True

    def test_add_comment_public_sets_private_false(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        note_resp = {"id": 557, "body": "pub", "private": False}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, note_resp)
        ) as mock_req:
            handler.add_comment("42", "pub", public=True)

        payload = mock_req.call_args.kwargs["json_body"]
        assert payload["private"] is False

    def test_add_comment_sanitizes_body(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        note_resp = {"id": 558, "body": "sanitized", "private": False}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, note_resp)
        ) as mock_req:
            handler.add_comment(
                "42",
                "See SRE-5678 and check #infra-alerts on us-south-3a-r02-10",
                public=True,
            )

        payload = mock_req.call_args.kwargs["json_body"]
        assert "SRE-5678" not in payload["body"]
        assert "us-south-3a-r02-10" not in payload["body"]
        assert "#infra-alerts" not in payload["body"]
        assert "[internal ticket]" in payload["body"]

    def test_add_comment_calls_correct_endpoint(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        note_resp = {"id": 559, "body": "ok"}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, note_resp)
        ) as mock_req:
            handler.add_comment("777", "test")

        mock_req.assert_called_once()
        assert mock_req.call_args[0][0] == "post"
        assert "/api/v2/tickets/777/notes" in mock_req.call_args[0][1]

    def test_add_comment_api_failure_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(500, text="Server Error")
        ):
            result = handler.add_comment("42", "test")

        assert result is None

    def test_add_comment_network_error_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch.object(handler, "_freshdesk_request", return_value=None):
            result = handler.add_comment("42", "test")

        assert result is None


@pytest.mark.unit
class TestFreshdeskRequest:
    """Tests for the _freshdesk_request helper."""

    def test_uses_api_key_as_basic_auth(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch("dc_support_mcp.vendors.iren.http_requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, {})
            handler._freshdesk_request("post", "/api/v2/tickets", json_body={"subject": "x"})

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["auth"] == (mock_credentials["password"], "X")

    def test_sets_content_type_json(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)

        with patch("dc_support_mcp.vendors.iren.http_requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {})
            handler._freshdesk_request("get", "/api/v2/tickets")

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    def test_request_exception_returns_none(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        import requests as http_requests

        with patch(
            "dc_support_mcp.vendors.iren.http_requests.post",
            side_effect=http_requests.ConnectionError("timeout"),
        ):
            result = handler._freshdesk_request("post", "/api/v2/tickets")

        assert result is None


@pytest.mark.unit
class TestFreshdeskPriorityMap:
    """Verify FRESHDESK_PRIORITY_MAP covers expected inputs."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("P1", 4),
            ("P2", 3),
            ("P3", 2),
            ("P4", 1),
            ("P5", 1),
            ("Critical", 4),
            ("High", 3),
            ("Moderate", 2),
            ("Medium", 2),
            ("Low", 1),
            ("Lowest", 1),
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 4),
        ],
    )
    def test_mapping(self, label, expected):
        assert FRESHDESK_PRIORITY_MAP[label] == expected

    def test_unknown_priority_uses_default(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path)
        freshdesk_resp = {"id": 1, "status": 2}

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(201, freshdesk_resp)
        ) as mock_req:
            handler.create_ticket(summary="x", description="y", priority="UnknownLevel")

        payload = mock_req.call_args.kwargs["json_body"]
        assert payload["priority"] == 2  # default fallback


# ── list_tickets REST API tests ──────────────────────────────────────


@pytest.mark.unit
class TestIrenListTicketsApi:
    """Tests for list_tickets via the Freshdesk REST API (#59)."""

    def test_basic_list(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        api_tickets = [
            {"id": 1, "subject": "First", "status": 2, "created_at": "2026-05-01T10:00:00Z",
             "responder_id": 999},
            {"id": 2, "subject": "Second", "status": 3, "created_at": "2026-05-01T11:00:00Z",
             "responder_id": None},
        ]

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, api_tickets)
        ):
            result = handler.list_tickets(limit=10)

        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["summary"] == "First"
        assert result[0]["status"] == "Open"
        assert result[0]["created"] == "2026-05-01T10:00:00Z"
        assert result[0]["assignee"] == "999"
        assert "support.iren.com/support/tickets/1" in result[0]["url"]
        assert result[1]["assignee"] == "Unassigned"

    def test_builds_correct_url_with_per_page(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, [])
        ) as mock_req:
            handler.list_tickets(limit=25)

        mock_req.assert_called_once()
        path_arg = mock_req.call_args[0][1]
        assert "per_page=25" in path_arg
        assert "page=1" in path_arg

    def test_status_filter(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, [])
        ) as mock_req:
            handler.list_tickets(status="open", limit=10)

        path_arg = mock_req.call_args[0][1]
        assert "status=2" in path_arg

    def test_status_filter_resolved(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, [])
        ) as mock_req:
            handler.list_tickets(status="resolved", limit=5)

        path_arg = mock_req.call_args[0][1]
        assert "status=4" in path_arg

    def test_unknown_status_filter_omitted(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, [])
        ) as mock_req:
            handler.list_tickets(status="nonexistent", limit=5)

        path_arg = mock_req.call_args[0][1]
        assert "status=" not in path_arg

    def test_pagination_for_large_limit(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        page1 = [{"id": i, "subject": f"T{i}", "status": 2} for i in range(1, 101)]
        page2 = [{"id": i, "subject": f"T{i}", "status": 2} for i in range(101, 151)]

        call_count = 0

        def side_effect(method, path):
            nonlocal call_count
            call_count += 1
            if "page=1" in path:
                return _mock_response(200, page1)
            if "page=2" in path:
                return _mock_response(200, page2)
            return _mock_response(200, [])

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.list_tickets(limit=150)

        assert len(result) == 150
        assert call_count == 2

    def test_limit_caps_results(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        api_tickets = [
            {"id": i, "subject": f"T{i}", "status": 2} for i in range(1, 50)
        ]

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(200, api_tickets)
        ):
            result = handler.list_tickets(limit=5)

        assert len(result) == 5

    def test_api_failure_falls_back_to_browser(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(500, text="Error")
        ), patch.object(
            handler, "_list_tickets_via_browser", return_value=[{"id": "99"}]
        ) as mock_browser:
            result = handler.list_tickets(limit=10)

        mock_browser.assert_called_once_with(status=None, limit=10)
        assert result == [{"id": "99"}]

    def test_api_none_response_falls_back_to_browser(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=None
        ), patch.object(
            handler, "_list_tickets_via_browser", return_value=[]
        ) as mock_browser:
            handler.list_tickets(limit=10)

        mock_browser.assert_called_once()

    def test_status_name_mapping(self):
        assert FRESHDESK_STATUS_MAP["open"] == 2
        assert FRESHDESK_STATUS_MAP["pending"] == 3
        assert FRESHDESK_STATUS_MAP["resolved"] == 4
        assert FRESHDESK_STATUS_MAP["closed"] == 5

    def test_status_names_reverse(self):
        assert FRESHDESK_STATUS_NAMES[2] == "Open"
        assert FRESHDESK_STATUS_NAMES[3] == "Pending"
        assert FRESHDESK_STATUS_NAMES[4] == "Resolved"
        assert FRESHDESK_STATUS_NAMES[5] == "Closed"

    def test_no_api_key_skips_api_uses_browser(self, mock_credentials, tmp_path):
        """Without IREN_FRESHDESK_API_KEY, list_tickets goes straight to browser."""
        handler = _make_iren_handler(mock_credentials, tmp_path)
        assert not handler._api_key

        with patch.object(
            handler, "_list_tickets_via_api"
        ) as mock_api, patch.object(
            handler, "_list_tickets_via_browser", return_value=[]
        ) as mock_browser:
            handler.list_tickets(limit=5)

        mock_api.assert_not_called()
        mock_browser.assert_called_once()


# ── get_ticket REST API tests ───────────────────────────────────────


@pytest.mark.unit
class TestIrenGetTicketApi:
    """Tests for get_ticket via the Freshdesk REST API (#60)."""

    def test_basic_get(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 42,
            "subject": "GPU overheating",
            "status": 2,
            "created_at": "2026-04-30T08:00:00Z",
            "requester_id": 12345,
            "requester": {"name": "Alice Smith", "email": "alice@example.com"},
            "responder_id": 67890,
        }
        conversations = [
            {
                "body_text": "The GPU is too hot",
                "from_email": "user@example.com",
                "created_at": "2026-04-30T08:01:00Z",
                "incoming": True,
            },
            {
                "body_text": "We are looking into it",
                "user_id": 67890,
                "created_at": "2026-04-30T09:00:00Z",
                "incoming": False,
            },
        ]

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, conversations)
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("42")

        assert result is not None
        assert result["id"] == "42"
        assert result["summary"] == "GPU overheating"
        assert result["status"] == "Open"
        assert result["created"] == "2026-04-30T08:00:00Z"
        assert result["reporter"] == "Alice Smith"
        assert result["assignee"] == "67890"
        assert len(result["comments"]) == 2
        assert result["comments"][0]["author"] == "user@example.com"
        assert result["comments"][0]["type"] == "customer-reply"
        assert result["comments"][1]["type"] == "agent-reply"
        assert "support.iren.com/support/tickets/42" in result["url"]

    def test_reporter_falls_back_to_email(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 43, "subject": "T", "status": 2,
            "created_at": "2026-05-01T00:00:00Z",
            "requester": {"email": "bob@example.com"},
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("43")

        assert result["reporter"] == "bob@example.com"

    def test_reporter_unknown_when_no_requester(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 44, "subject": "T", "status": 2,
            "created_at": "2026-05-01T00:00:00Z",
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("44")

        assert result["reporter"] == "Unknown"

    def test_no_conversations(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 10, "subject": "Test", "status": 4,
            "created_at": "2026-05-01T00:00:00Z",
            "requester_id": 1, "responder_id": None,
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("10")

        assert result is not None
        assert result["status"] == "Resolved"
        assert result["assignee"] == "Unassigned"
        assert result["comments"] == []

    def test_conversations_api_failure_still_returns_ticket(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 5, "subject": "X", "status": 3,
            "created_at": "2026-05-01T00:00:00Z",
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(500, text="Error")
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("5")

        assert result is not None
        assert result["comments"] == []
        assert result["status"] == "Pending"

    def test_api_failure_falls_back_to_browser(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=_mock_response(404, text="Not found")
        ), patch.object(
            handler, "_get_ticket_via_browser", return_value={"id": "42", "summary": "browser"}
        ) as mock_browser:
            result = handler.get_ticket("42")

        mock_browser.assert_called_once_with("42")
        assert result["summary"] == "browser"

    def test_api_none_response_falls_back_to_browser(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")

        with patch.object(
            handler, "_freshdesk_request", return_value=None
        ), patch.object(
            handler, "_get_ticket_via_browser", return_value=None
        ) as mock_browser:
            handler.get_ticket("99")

        mock_browser.assert_called_once_with("99")

    def test_conversation_author_fallback_to_user_id(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {"id": 7, "subject": "T", "status": 2, "created_at": "2026-05-01T00:00:00Z"}
        conversations = [
            {"body_text": "note", "user_id": 555, "created_at": "2026-05-01T01:00:00Z",
             "incoming": False},
        ]

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, conversations)
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("7")

        assert result["comments"][0]["author"] == "555"

    def test_include_requester_in_api_url(self, mock_credentials, tmp_path):
        """Verify the API call includes ?include=requester."""
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {"id": 8, "subject": "T", "status": 2, "created_at": "2026-05-01T00:00:00Z"}

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect) as mock_req:
            handler.get_ticket("8")

        ticket_call = [c for c in mock_req.call_args_list if "/conversations" not in c[0][1]][0]
        assert "include=requester" in ticket_call[0][1]

    def test_description_from_description_text(self, mock_credentials, tmp_path):
        """description_text is preferred when available."""
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 50, "subject": "T", "status": 2,
            "created_at": "2026-05-01T00:00:00Z",
            "description_text": "Plain text description body",
            "description": "<p>Plain text description body</p>",
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("50")

        assert result is not None
        assert result["description"] == "Plain text description body"

    def test_description_falls_back_to_html_stripped(self, mock_credentials, tmp_path):
        """When description_text is empty, HTML tags are stripped from description."""
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 51, "subject": "T", "status": 2,
            "created_at": "2026-05-01T00:00:00Z",
            "description_text": "",
            "description": "<div>GPU <b>overheating</b> on node 5</div>",
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("51")

        assert result is not None
        assert result["description"] == "GPU overheating on node 5"

    def test_description_empty_when_missing(self, mock_credentials, tmp_path):
        """description is empty string when neither field is present."""
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {
            "id": 52, "subject": "T", "status": 2,
            "created_at": "2026-05-01T00:00:00Z",
        }

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, [])
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            result = handler.get_ticket("52")

        assert result is not None
        assert result["description"] == ""


# ── _parse_ticket_from_page browser fallback tests ────────────────────


FRESHDESK_TICKET_PAGE_HTML = """<!DOCTYPE html>
<html>
<head><title>[#4018] congested bandwidth : Support</title></head>
<body>
<div class="ticket-detail">
  <h1>congested bandwidth #4018</h1>
  <p class="ticket-meta">Created on Sat, 2 May, 2026 at 1:51 AM - via Phone</p>
  <span class="fw-status-badge">Awaiting your Reply</span>

  <div class="communications">
    <div class="communication-item incoming">
      <div class="communication-header">
        <strong class="name">Atharva Chandratre</strong>
        <time datetime="2026-05-02T01:51:00Z">about 13 hours ago</time>
      </div>
      <div class="communication-body">
        Hi,
        I see congested bandwidth for the following hosts:
        gpu-id-x75c-h6fw (borderel) | cavila-os-h200-106 | PG13B-8-5-HPC
      </div>
    </div>
    <div class="communication-item reply">
      <div class="communication-header">
        <strong class="name">Akhil Sharma</strong>
        <time datetime="2026-05-02T10:30:00Z">about 4 hours ago</time>
      </div>
      <div class="communication-body">
        Cheers,
        We have identified the issue and are working on a fix.
      </div>
    </div>
  </div>
</div>
</body>
</html>"""

FRESHDESK_TICKET_PAGE_ALT_HTML = """<!DOCTYPE html>
<html>
<head><title>[#5001] GPU down : Support</title></head>
<body>
<div class="ticket-content">
  <h1>GPU down #5001</h1>
  <span>Created on Mon, 1 Apr, 2026 at 9:00 AM - via Email</span>
  <div class="fw-status-badge">Open</div>

  <div class="ticket-communications">
    <div data-conversation-id="100" class="conversation-entry">
      <span class="author-name">Bob Smith</span>
      <span class="timestamp">2 days ago</span>
      <div class="content-body">GPU-42 is reporting errors. Please investigate.</div>
    </div>
    <div data-conversation-id="101" class="conversation-entry note">
      <span class="author-name">Agent Jane</span>
      <span class="timestamp">1 day ago</span>
      <div class="content-body">Internal: checking XID logs for this node.</div>
    </div>
    <div data-conversation-id="102" class="conversation-entry">
      <span class="author-name">Agent Jane</span>
      <span class="timestamp">5 hours ago</span>
      <div class="content-body">We replaced the GPU. Please confirm it's working.</div>
    </div>
  </div>
</div>
</body>
</html>"""

FRESHDESK_TICKET_PAGE_WITH_DESC_HTML = """<!DOCTYPE html>
<html>
<head><title>[#6001] NVLink failure : Support</title></head>
<body>
<div class="ticket-detail">
  <h1>NVLink failure #6001</h1>
  <p>Created on Mon, 4 May, 2026 at 10:00 AM - via Portal</p>
  <span class="fw-status-badge">Open</span>
  <div class="fw-ticket-description">
    GPU-42 on node PG24A-1-2-HPC reports persistent NVLink errors. XID 74 observed in dmesg.
  </div>
  <div class="communications">
    <div class="communication-item incoming">
      <div class="communication-header">
        <strong class="name">Alice</strong>
        <time datetime="2026-05-04T10:00:00Z">just now</time>
      </div>
      <div class="communication-body">Please investigate the NVLink issue.</div>
    </div>
  </div>
</div>
</body>
</html>"""

FRESHDESK_TICKET_PAGE_NO_COMMENTS_HTML = """<!DOCTYPE html>
<html>
<head><title>[#9999] New ticket : Support</title></head>
<body>
<div class="ticket-detail">
  <h1>New ticket #9999</h1>
  <p>Created on Fri, 1 May, 2026 at 8:00 AM - via Portal</p>
  <span class="fw-status-badge">Open</span>
  <div class="communications"></div>
</div>
</body>
</html>"""


@pytest.mark.unit
class TestParseTicketFromPage:
    """Tests for _parse_ticket_from_page with realistic Freshdesk portal HTML fixtures."""

    def _setup_handler_with_page(self, mock_credentials, tmp_path, html: str):
        """Create handler with a mock page that evaluates JS against given HTML."""
        handler = _make_iren_handler(mock_credentials, tmp_path)
        mock_page = MagicMock()

        import re as re_mod

        def _extract_blocks(html_str: str) -> list[tuple[str, str]]:
            """Extract (classes, inner_content) for communication/conversation divs."""
            # Match opening tags for communication-item or conversation-entry, then
            # collect everything until we count balanced div open/close.
            pattern = re_mod.compile(
                r'<div[^>]*class="([^"]*(?:communication-item|conversation-entry)[^"]*)"'
                r"[^>]*>",
            )
            blocks = []
            for m in pattern.finditer(html_str):
                classes = m.group(1)
                start = m.end()
                depth = 1
                pos = start
                while depth > 0 and pos < len(html_str):
                    open_m = re_mod.search(r"<div[\s>]", html_str[pos:])
                    close_m = re_mod.search(r"</div>", html_str[pos:])
                    if close_m is None:
                        break
                    if open_m and open_m.start() < close_m.start():
                        depth += 1
                        pos += open_m.end()
                    else:
                        depth -= 1
                        if depth == 0:
                            blocks.append((classes, html_str[start : pos + close_m.start()]))
                        pos += close_m.end()
            return blocks

        def _extract_data_conv_blocks(html_str: str) -> list[tuple[str, str]]:
            """Extract blocks using data-conversation-id attribute."""
            pattern = re_mod.compile(
                r'<div[^>]*data-conversation-id="[^"]*"[^>]*class="([^"]*)"[^>]*>',
            )
            blocks = []
            for m in pattern.finditer(html_str):
                classes = m.group(1)
                start = m.end()
                depth = 1
                pos = start
                while depth > 0 and pos < len(html_str):
                    open_m = re_mod.search(r"<div[\s>]", html_str[pos:])
                    close_m = re_mod.search(r"</div>", html_str[pos:])
                    if close_m is None:
                        break
                    if open_m and open_m.start() < close_m.start():
                        depth += 1
                        pos += open_m.end()
                    else:
                        depth -= 1
                        if depth == 0:
                            blocks.append((classes, html_str[start : pos + close_m.start()]))
                        pos += close_m.end()
            return blocks

        def mock_evaluate(js_code):
            """Simulate the JS extraction logic in Python for testing."""
            result = {
                "summary": "",
                "status": "",
                "created": "",
                "reporter": "",
                "assignee": "",
                "description": "",
                "comments": [],
            }

            # Extract h1
            h1_match = re_mod.search(r"<h1>(.*?)</h1>", html)
            if h1_match:
                result["summary"] = h1_match.group(1)

            # Extract status badge
            badge_match = re_mod.search(
                r'class="fw-status-badge"[^>]*>(.*?)</', html
            )
            if badge_match:
                result["status"] = badge_match.group(1).strip()

            # Extract created date
            created_match = re_mod.search(r"Created on [^<]+", html)
            if created_match:
                result["created"] = created_match.group(0).strip()

            # Extract reporter from "X reported" pattern
            reporter_match = re_mod.search(r"(\w[\w\s]+?)\s+reported\s", html)
            if reporter_match:
                result["reporter"] = reporter_match.group(1).strip()

            # Extract description from dedicated element or first body
            for desc_cls in ["fw-ticket-description", "ticket-description", "ticket-body", "fr-view"]:
                desc_match = re_mod.search(
                    rf'class="[^"]*{desc_cls}[^"]*"[^>]*>(.*?)</div>',
                    html,
                    re_mod.DOTALL,
                )
                if desc_match:
                    result["description"] = re_mod.sub(r"<[^>]+>", "", desc_match.group(1)).strip()
                    break
            if not result["description"]:
                first_body = re_mod.search(
                    r'class="(?:communication-body|content-body)"[^>]*>(.*?)</div>',
                    html,
                    re_mod.DOTALL,
                )
                if first_body:
                    result["description"] = re_mod.sub(r"<[^>]+>", "", first_body.group(1)).strip()

            # Extract comment blocks
            blocks = _extract_blocks(html)
            if not blocks:
                blocks = _extract_data_conv_blocks(html)

            for classes, content in blocks:
                entry = {"author": "Unknown", "date": "Unknown", "comment": "", "type": "comment"}

                # Author
                author_match = re_mod.search(
                    r'class="(?:name|author-name)"[^>]*>(.*?)</', content
                )
                if author_match:
                    entry["author"] = author_match.group(1).strip()

                # Date
                time_match = re_mod.search(r"<time[^>]*>(.*?)</time>", content)
                if time_match:
                    entry["date"] = time_match.group(1).strip()
                else:
                    ts_match = re_mod.search(
                        r'class="timestamp"[^>]*>(.*?)</', content
                    )
                    if ts_match:
                        entry["date"] = ts_match.group(1).strip()

                # Body
                body_match = re_mod.search(
                    r'class="(?:communication-body|content-body)"[^>]*>(.*?)</div>',
                    content,
                    re_mod.DOTALL,
                )
                if body_match:
                    body_text = re_mod.sub(r"<[^>]+>", "", body_match.group(1))
                    entry["comment"] = body_text.strip()

                # Type
                if "incoming" in classes:
                    entry["type"] = "customer-reply"
                elif "note" in classes:
                    entry["type"] = "note"
                else:
                    entry["type"] = "agent-reply"

                if entry["comment"]:
                    result["comments"].append(entry)

            return result

        mock_page.evaluate = mock_evaluate
        mock_page.locator = MagicMock(return_value=MagicMock(count=MagicMock(return_value=0)))
        handler._page = mock_page
        return handler

    def test_extracts_comments_from_communication_items(self, mock_credentials, tmp_path):
        """Comments are extracted from .communication-item elements."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert len(result["comments"]) == 2
        assert result["comments"][0]["author"] == "Atharva Chandratre"
        assert result["comments"][0]["date"] == "about 13 hours ago"
        assert "congested bandwidth" in result["comments"][0]["comment"]
        assert result["comments"][0]["type"] == "customer-reply"
        assert result["comments"][1]["author"] == "Akhil Sharma"
        assert result["comments"][1]["type"] == "agent-reply"
        assert "identified the issue" in result["comments"][1]["comment"]

    def test_extracts_summary_and_status(self, mock_credentials, tmp_path):
        """Summary from h1, status from .fw-status-badge."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert "congested bandwidth" in result["summary"]
        assert result["status"] == "Awaiting your Reply"

    def test_extracts_created_date(self, mock_credentials, tmp_path):
        """Created date has 'Created on' prefix stripped."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert "Sat, 2 May, 2026" in result["created"]
        assert not result["created"].startswith("Created")

    def test_extracts_from_data_conversation_id(self, mock_credentials, tmp_path):
        """Alternative HTML with data-conversation-id attributes."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_ALT_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert result["status"] == "Open"
        assert "GPU down" in result["summary"]
        assert len(result["comments"]) == 3
        assert result["comments"][0]["author"] == "Bob Smith"
        assert "GPU-42" in result["comments"][0]["comment"]
        assert result["comments"][1]["type"] == "agent-reply"
        assert result["comments"][2]["author"] == "Agent Jane"

    def test_no_comments_returns_empty_list(self, mock_credentials, tmp_path):
        """Empty communications div returns no comments."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_NO_COMMENTS_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert result["comments"] == []
        assert result["status"] == "Open"
        assert "New ticket" in result["summary"]

    def test_extracts_description_from_dedicated_element(self, mock_credentials, tmp_path):
        """Description from .fw-ticket-description element."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_WITH_DESC_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert "NVLink errors" in result["description"]
        assert "XID 74" in result["description"]

    def test_description_fallback_to_first_body(self, mock_credentials, tmp_path):
        """Without a dedicated description element, uses first communication-body."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert result["description"] != ""
        assert "congested bandwidth" in result["description"]

    def test_description_empty_when_no_body(self, mock_credentials, tmp_path):
        """Empty description when no body elements present."""
        handler = self._setup_handler_with_page(
            mock_credentials, tmp_path, FRESHDESK_TICKET_PAGE_NO_COMMENTS_HTML
        )
        result = handler._parse_ticket_from_page()

        assert result is not None
        assert result["description"] == ""

    def test_returns_none_when_no_page(self, mock_credentials, tmp_path):
        """Returns None when _page is None."""
        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler._page = None
        assert handler._parse_ticket_from_page() is None

    def test_handles_evaluate_exception(self, mock_credentials, tmp_path):
        """Returns None when page.evaluate raises."""
        handler = _make_iren_handler(mock_credentials, tmp_path)
        mock_page = MagicMock()
        mock_page.evaluate = MagicMock(side_effect=Exception("JS error"))
        handler._page = mock_page

        result = handler._parse_ticket_from_page()
        assert result is None
