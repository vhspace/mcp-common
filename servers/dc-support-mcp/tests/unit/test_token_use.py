"""Token-use improvements: comment bounding, KB slimming, limit clamps, error caps.

Covers the changes from the token-use review on PR #79:
  1. ``get_vendor_ticket`` / ``get_ticket`` bound the comment thread.
  2. The CLI ``get-ticket`` text view renders comments compactly.
  3. ``list_vendor_tickets`` / ``search_vendor_kb`` clamp ``limit``.
  4. ``search_knowledge_base`` returns slim rows (id/title/url/category).
  5. ``create_service_desk_request`` caps the vendor error blob to ~200 chars.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dc_support_mcp.cli import COMMENT_PREVIEW_CHARS, app
from dc_support_mcp.vendor_handler import DEFAULT_MAX_COMMENTS, bound_comments
from tests.unit.test_iren_handler import _make_iren_handler, _mock_response

runner = CliRunner()


def _atlassian_req_details(n: int) -> dict:
    """Build a reqDetails payload with *n* chronological worker comments c0..c{n-1}."""
    return {
        "issue": {
            "key": "SUPP-1",
            "summary": "s",
            "status": "Open",
            "reporter": {"displayName": "reporter"},
            "assignee": {"displayName": "assignee"},
            "friendlyDate": "01/Jan/26",
            "activityStream": [
                {
                    "type": "worker-comment",
                    "author": f"author{i}",
                    "friendlyDate": f"date{i}",
                    "rawComment": f"c{i}",
                }
                for i in range(n)
            ],
        }
    }


# ── 1. bound_comments pure helper ─────────────────────────────────────


@pytest.mark.unit
class TestBoundComments:
    def test_keeps_most_recent_n(self):
        comments = [{"comment": f"c{i}"} for i in range(15)]
        kept, total, truncated = bound_comments(comments, max_comments=10)
        assert total == 15
        assert truncated is True
        assert len(kept) == 10
        # Tail = newest, original order preserved.
        assert kept[0]["comment"] == "c5"
        assert kept[-1]["comment"] == "c14"

    def test_no_truncation_when_under_cap(self):
        comments = [{"comment": "a"}, {"comment": "b"}]
        kept, total, truncated = bound_comments(comments, max_comments=10)
        assert total == 2
        assert truncated is False
        assert len(kept) == 2

    def test_include_comments_false_drops_all_but_reports_total(self):
        comments = [{"comment": f"c{i}"} for i in range(4)]
        kept, total, truncated = bound_comments(comments, include_comments=False)
        assert kept == []
        assert total == 4
        assert truncated is True

    def test_negative_max_means_no_cap(self):
        comments = [{"comment": f"c{i}"} for i in range(30)]
        kept, _total, truncated = bound_comments(comments, max_comments=-1)
        assert len(kept) == 30
        assert truncated is False

    def test_zero_max_keeps_none_but_flags_truncated(self):
        comments = [{"comment": "a"}]
        kept, total, truncated = bound_comments(comments, max_comments=0)
        assert kept == []
        assert total == 1
        assert truncated is True

    def test_returns_copies_not_references(self):
        comments = [{"comment": "a"}]
        kept, _, _ = bound_comments(comments, max_comments=10)
        kept[0]["comment"] = "mutated"
        assert comments[0]["comment"] == "a"


@pytest.mark.unit
class TestApplyCommentBounds:
    def test_attaches_signal_keys(self, ori_handler):
        ticket = {"id": "X", "comments": [{"comment": f"c{i}"} for i in range(12)]}
        out = ori_handler._apply_comment_bounds(ticket, max_comments=10)
        assert out["comments_total"] == 12
        assert out["comments_truncated"] is True
        assert len(out["comments"]) == 10

    def test_missing_comments_key_is_safe(self, ori_handler):
        ticket = {"id": "X", "summary": "browser"}
        out = ori_handler._apply_comment_bounds(ticket)
        assert out["comments"] == []
        assert out["comments_total"] == 0
        assert out["comments_truncated"] is False


# ── 1. Atlassian get_ticket bounding ──────────────────────────────────


@pytest.mark.unit
class TestAtlassianGetTicketBounding:
    def test_caps_to_max_comments_and_keeps_newest(self, ori_handler):
        with patch.object(
            ori_handler,
            "_make_api_request",
            return_value={"reqDetails": _atlassian_req_details(15)},
        ):
            ticket = ori_handler.get_ticket("SUPP-1", max_comments=10)

        assert ticket is not None
        assert ticket["comments_total"] == 15
        assert ticket["comments_truncated"] is True
        assert len(ticket["comments"]) == 10
        assert ticket["comments"][-1]["comment"] == "c14"
        assert ticket["comments"][0]["comment"] == "c5"

    def test_default_cap_is_ten(self, ori_handler):
        with patch.object(
            ori_handler,
            "_make_api_request",
            return_value={"reqDetails": _atlassian_req_details(25)},
        ):
            ticket = ori_handler.get_ticket("SUPP-1")
        assert len(ticket["comments"]) == DEFAULT_MAX_COMMENTS
        assert ticket["comments_truncated"] is True

    def test_include_comments_false_drops_bodies(self, ori_handler):
        with patch.object(
            ori_handler, "_make_api_request", return_value={"reqDetails": _atlassian_req_details(5)}
        ):
            ticket = ori_handler.get_ticket("SUPP-1", include_comments=False)
        assert ticket["comments"] == []
        # Atlassian bundles the thread with the ticket, so the true count is known.
        assert ticket["comments_total"] == 5
        assert ticket["comments_truncated"] is True

    def test_small_thread_unchanged(self, ori_handler):
        with patch.object(
            ori_handler, "_make_api_request", return_value={"reqDetails": _atlassian_req_details(2)}
        ):
            ticket = ori_handler.get_ticket("SUPP-1")
        assert len(ticket["comments"]) == 2
        assert ticket["comments_truncated"] is False


# ── 1. IREN get_ticket bounding ───────────────────────────────────────


@pytest.mark.unit
class TestIrenGetTicketBounding:
    def _conversations(self, n):
        return [
            {
                "body_text": f"c{i}",
                "from_email": "user@example.com",
                "created_at": f"t{i}",
                "incoming": True,
            }
            for i in range(n)
        ]

    def test_caps_to_max_comments_and_keeps_newest(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {"id": 42, "subject": "T", "status": 2, "created_at": "t"}

        def side_effect(method, path):
            if "/conversations" in path:
                return _mock_response(200, self._conversations(15))
            return _mock_response(200, ticket_data)

        with patch.object(handler, "_freshdesk_request", side_effect=side_effect):
            ticket = handler.get_ticket("42", max_comments=10)

        assert ticket["comments_total"] == 15
        assert ticket["comments_truncated"] is True
        assert len(ticket["comments"]) == 10
        assert ticket["comments"][-1]["comment"] == "c14"

    def test_include_comments_false_skips_conversation_fetch(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        ticket_data = {"id": 42, "subject": "T", "status": 2, "created_at": "t"}

        with (
            patch.object(
                handler, "_freshdesk_request", return_value=_mock_response(200, ticket_data)
            ),
            patch.object(handler, "_fetch_conversations") as mock_fetch,
        ):
            ticket = handler.get_ticket("42", include_comments=False)

        mock_fetch.assert_not_called()
        assert ticket["comments"] == []
        assert ticket["comments_total"] == 0
        assert ticket["comments_truncated"] is False

    def test_browser_fallback_is_bounded(self, mock_credentials, tmp_path):
        handler = _make_iren_handler(mock_credentials, tmp_path, api_key="test-key")
        browser_ticket = {
            "id": "42",
            "summary": "browser",
            "comments": [{"author": "a", "date": "d", "comment": f"c{i}"} for i in range(12)],
        }

        with (
            patch.object(handler, "_freshdesk_request", return_value=_mock_response(404)),
            patch.object(handler, "_get_ticket_via_browser", return_value=browser_ticket),
        ):
            ticket = handler.get_ticket("42", max_comments=5)

        assert len(ticket["comments"]) == 5
        assert ticket["comments_total"] == 12
        assert ticket["comments_truncated"] is True


# ── 4. search_knowledge_base slim rows ────────────────────────────────


@pytest.mark.unit
class TestSearchKnowledgeBaseSlim:
    def test_returns_only_id_title_url_category(self, mock_credentials, tmp_path):
        from datetime import datetime

        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler._kb_cache = {
            "articles": [
                {
                    "id": "1",
                    "title": "Network Troubleshooting",
                    "url": "https://example.com/1",
                    "category": "Networking",
                    "last_modified": "2025-01-01",
                    "content": "lots of text " * 50,
                    "attachments": [{"name": "x.pdf", "url": "u", "content_type": None, "size": 1}],
                },
            ],
            "cached_at": datetime.now(),
            "last_modified": None,
        }
        handler._save_kb_cache(handler._kb_cache["articles"])

        results = handler.search_knowledge_base("network")
        assert len(results) == 1
        row = results[0]
        assert set(row.keys()) == {"id", "title", "url", "category"}
        assert "content" not in row
        assert "attachments" not in row
        assert row["category"] == "Networking"

    def test_respects_limit(self, mock_credentials, tmp_path):
        from datetime import datetime

        handler = _make_iren_handler(mock_credentials, tmp_path)
        handler._kb_cache = {
            "articles": [
                {
                    "id": str(i),
                    "title": f"network guide {i}",
                    "url": f"https://example.com/{i}",
                    "category": None,
                    "last_modified": None,
                    "content": None,
                    "attachments": [],
                }
                for i in range(10)
            ],
            "cached_at": datetime.now(),
            "last_modified": None,
        }
        handler._save_kb_cache(handler._kb_cache["articles"])

        results = handler.search_knowledge_base("network", limit=3)
        assert len(results) == 3


# ── 5. create_service_desk_request error blob cap ─────────────────────


@pytest.mark.unit
class TestCreateServiceDeskErrorCap:
    def test_error_blob_capped_to_200(self, ori_handler):
        # ORI overrides create_service_desk_request with browser automation, so
        # exercise the base Atlassian REST method directly (as #35's tests do).
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        with patch.object(ori_handler.session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = "x" * 500
            mock_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request"
            mock_post.return_value = mock_resp

            result = AtlassianServiceDeskHandler.create_service_desk_request(
                ori_handler, summary="s", description="d"
            )

        assert result is None
        assert ori_handler.last_error == "HTTP 400: " + "x" * 200
        assert "x" * 201 not in ori_handler.last_error


# ── MCP tool wiring: get_vendor_ticket params + limit clamps ──────────


@pytest.mark.unit
class TestMcpGetVendorTicketParams:
    def test_passes_bounding_params_to_handler(self):
        from dc_support_mcp.mcp_server import get_vendor_ticket

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.get_ticket.return_value = {"id": "X", "comments": []}
            mock_get.return_value = mock_handler

            get_vendor_ticket(ticket_id="X", vendor="ori", include_comments=False, max_comments=3)
            mock_handler.get_ticket.assert_called_once_with(
                "X", include_comments=False, max_comments=3
            )

    def test_default_max_comments_wired(self):
        from dc_support_mcp.mcp_server import get_vendor_ticket

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.get_ticket.return_value = {"id": "X", "comments": []}
            mock_get.return_value = mock_handler

            get_vendor_ticket(ticket_id="X", vendor="ori")
            mock_handler.get_ticket.assert_called_once_with(
                "X", include_comments=True, max_comments=DEFAULT_MAX_COMMENTS
            )


@pytest.mark.unit
class TestMcpLimitClamp:
    def test_list_vendor_tickets_clamps_high(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.last_error = None
            mock_handler.list_tickets.return_value = [{"id": "1"}]
            mock_handler.list_more_signal.return_value = {"has_more": False, "total": 1}
            mock_get.return_value = mock_handler

            list_vendor_tickets(vendor="ori", limit=9999)
            mock_handler.list_tickets.assert_called_once_with(status="open", limit=100)

    def test_list_vendor_tickets_clamps_low(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.last_error = None
            mock_handler.list_tickets.return_value = [{"id": "1"}]
            mock_handler.list_more_signal.return_value = {"has_more": False, "total": 1}
            mock_get.return_value = mock_handler

            list_vendor_tickets(vendor="ori", limit=0)
            mock_handler.list_tickets.assert_called_once_with(status="open", limit=1)

    def test_search_vendor_kb_clamps_high(self):
        from dc_support_mcp.mcp_server import search_vendor_kb

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.last_error = None
            mock_handler.search_knowledge_base.return_value = []
            mock_get.return_value = mock_handler

            search_vendor_kb(query="x", vendor="iren", limit=9999)
            mock_handler.search_knowledge_base.assert_called_once_with("x", limit=50)


# ── 2. CLI get-ticket compact rendering ───────────────────────────────


def _ticket_with_comments(comments, total, truncated):
    return {
        "id": "SUPP-1",
        "summary": "GPU down",
        "status": "Open",
        "assignee": "Joey",
        "url": "https://example.com/SUPP-1",
        "comments": comments,
        "comments_total": total,
        "comments_truncated": truncated,
    }


@pytest.mark.unit
class TestCliGetTicketCompact:
    @patch("dc_support_mcp.cli._get_handler")
    def test_renders_comments_newest_first_compactly(self, mock_get_handler):
        comments = [
            {"author": "Alice", "date": "day1", "comment": "the oldest message"},
            {"author": "Bob", "date": "day2", "comment": "the newest message"},
        ]
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments(
            comments, total=5, truncated=True
        )
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori"])
        assert result.exit_code == 0, result.output
        out = result.output
        assert "comments: 2 of 5 (newest first)" in out
        # Newest first: Bob's line precedes Alice's.
        assert out.index("Bob · day2") < out.index("Alice · day1")
        assert "the newest message" in out
        # The whole-list collapse ("[N items]") must NOT appear.
        assert "[2 items]" not in out

    @patch("dc_support_mcp.cli._get_handler")
    def test_long_comment_body_is_truncated(self, mock_get_handler):
        long_body = "y" * 400
        comments = [{"author": "Alice", "date": "day1", "comment": long_body}]
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments(
            comments, total=1, truncated=False
        )
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori"])
        assert result.exit_code == 0, result.output
        assert "…" in result.output
        assert "y" * 200 not in result.output
        # Preview is bounded by COMMENT_PREVIEW_CHARS.
        assert ("y" * COMMENT_PREVIEW_CHARS) not in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_no_comments_message(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments([], total=0, truncated=False)
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori"])
        assert result.exit_code == 0, result.output
        assert "comments: none" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_dropped_comments_hint_when_zero_shown(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments([], total=8, truncated=True)
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori", "--no-comments"])
        assert result.exit_code == 0, result.output
        assert "0 of 8 shown" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_max_comments_option_passed_through(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments([], total=0, truncated=False)
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["get-ticket", "SUPP-1", "--vendor", "ori", "--max-comments", "3"]
        )
        assert result.exit_code == 0, result.output
        mock_handler.get_ticket.assert_called_once_with(
            "SUPP-1", include_comments=True, max_comments=3
        )

    @patch("dc_support_mcp.cli._get_handler")
    def test_no_comments_flag_passed_through(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments([], total=0, truncated=False)
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori", "--no-comments"])
        assert result.exit_code == 0, result.output
        mock_handler.get_ticket.assert_called_once_with(
            "SUPP-1", include_comments=False, max_comments=DEFAULT_MAX_COMMENTS
        )

    @patch("dc_support_mcp.cli._get_handler")
    def test_json_output_dumps_full_structure(self, mock_get_handler):
        comments = [{"author": "Bob", "date": "day2", "comment": "msg"}]
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = _ticket_with_comments(
            comments, total=5, truncated=True
        )
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1", "--vendor", "ori", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["comments_total"] == 5
        assert data["comments_truncated"] is True
        assert len(data["comments"]) == 1
