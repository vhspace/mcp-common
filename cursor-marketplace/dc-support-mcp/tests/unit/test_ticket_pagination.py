"""Ticket-listing pagination + ``has_more``/``total`` more-signal (issue #93).

Covers the Atlassian REST pagination loop, the fetch-one-extra truncation
signal, and the CLI / MCP plumbing that surfaces ``has_more`` and ``total``.
All HTTP/API access is mocked — the suite stays hermetic and offline.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dc_support_mcp.cli import app

runner = CliRunner()


# ── Atlassian REST pagination ────────────────────────────────────────
#
# These mocks model the REAL live ``allReqFilter`` customer-models backend
# (verified against ORI for #94):
#   * the page index is read from ``selectedPage`` (NOT ``page`` — the old
#     code's ``page`` key was silently ignored, which is the blocker #94
#     fixed),
#   * ``resultsPerPage`` is server-capped at 20 (we cannot enlarge a page),
#   * every response reports the real ``totalResults`` / ``totalPages``.

_PAGE_SIZE = 20


def _page_rows(handler, keys):
    """Build ``requestList`` rows for the given ticket *keys*."""
    portal = handler.PORTAL_ID
    return [
        {
            "key": key,
            "summary": f"Summary for {key}",
            "status": "Open",
            "friendlyDate": "1/Jan/26 1:00 AM",
            "assignee": "Unassigned",
            "portalBaseUrl": f"/servicedesk/customer/portal/{portal}",
        }
        for key in keys
    ]


def _arf_response(handler, rows, *, total, total_pages, selected_page):
    """Build an ``allReqFilter`` response with rows + paging metadata."""
    return {
        "allReqFilter": {
            "requestList": rows,
            "totalResults": total,
            "totalPages": total_pages,
            "selectedPage": selected_page,
            "resultsPerPage": _PAGE_SIZE,
        },
        "xsrfToken": "test-token",
    }


def _make_paginator(handler, all_keys, *, total=None):
    """``_make_api_request`` side-effect that paginates *all_keys* by ``selectedPage``.

    Models the working backend: 20 rows/page, honours ``selectedPage``, and
    reports a real total (``len(all_keys)`` unless *total* overrides it, to
    simulate a window narrower than the true backend total).
    """
    reported_total = len(all_keys) if total is None else total
    total_pages = max(1, (reported_total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def side_effect(payload):
        sp = payload["options"]["allReqFilter"]["selectedPage"]
        start = (sp - 1) * _PAGE_SIZE
        rows = _page_rows(handler, all_keys[start : start + _PAGE_SIZE])
        return _arf_response(
            handler, rows, total=reported_total, total_pages=total_pages, selected_page=sp
        )

    return side_effect


def _make_page_ignoring_backend(handler, keys, *, total, total_pages):
    """``_make_api_request`` side-effect modelling the #94 blocker.

    The backend IGNORES ``selectedPage`` and always re-serves the same first
    page, yet still reports the real ``totalResults``. The page-walk must
    stop (not loop) and must NOT claim the capped result is complete.
    """

    def side_effect(payload):
        return _arf_response(
            handler,
            _page_rows(handler, keys),
            total=total,
            total_pages=total_pages,
            selected_page=1,
        )

    return side_effect


def _keys(handler, start, stop):
    """Inclusive-exclusive range of vendor ticket keys, e.g. SUPP-1 .. SUPP-20."""
    return [f"{handler.TICKET_ID_PREFIX}-{i}" for i in range(start, stop)]


@pytest.mark.unit
class TestAtlassianPagination:
    """The Atlassian REST path walks ``selectedPage`` instead of capping at page 1."""

    def test_paginates_beyond_single_server_page(self, ori_handler):
        all_keys = _keys(ori_handler, 1, 46)  # 45 tickets → 3 pages (20/20/5)
        with patch.object(
            ori_handler, "_make_api_request", side_effect=_make_paginator(ori_handler, all_keys)
        ) as mock_api:
            result = ori_handler.list_tickets(status="open", limit=50)

        assert len(result) == 45  # > a single ~20-row server page
        assert mock_api.call_count == 3  # walked three pages via selectedPage
        assert ori_handler.last_list_has_more is False  # source exhausted (45 of 45)
        assert ori_handler.last_list_total == 45  # real backend total
        ids = {t["id"] for t in result}
        assert f"{ori_handler.TICKET_ID_PREFIX}-1" in ids
        assert f"{ori_handler.TICKET_ID_PREFIX}-45" in ids

    def test_walks_selected_page_not_page_param(self, ori_handler):
        """The page index must travel in ``selectedPage`` (the honoured key)."""
        seen_payload_keys: list[set[str]] = []

        def side_effect(payload):
            opts = payload["options"]["allReqFilter"]
            seen_payload_keys.append(set(opts.keys()))
            sp = opts["selectedPage"]
            start = (sp - 1) * _PAGE_SIZE
            rows = _page_rows(ori_handler, _keys(ori_handler, 1, 41)[start : start + _PAGE_SIZE])
            return _arf_response(ori_handler, rows, total=40, total_pages=2, selected_page=sp)

        with patch.object(ori_handler, "_make_api_request", side_effect=side_effect):
            ori_handler.list_tickets(status="open", limit=40)

        assert all("selectedPage" in keys for keys in seen_payload_keys)
        assert all("page" not in keys for keys in seen_payload_keys)

    def test_caps_at_limit_and_flags_has_more(self, ori_handler):
        all_keys = _keys(ori_handler, 1, 81)  # 80 tickets → 4 pages
        with patch.object(
            ori_handler, "_make_api_request", side_effect=_make_paginator(ori_handler, all_keys)
        ):
            result = ori_handler.list_tickets(status="open", limit=50)

        assert len(result) == 50
        assert ori_handler.last_list_has_more is True
        assert ori_handler.last_list_total == 80

    def test_limit_5_with_more_available(self, ori_handler):
        all_keys = _keys(ori_handler, 1, 21)  # 20 available on page 1
        with patch.object(
            ori_handler, "_make_api_request", side_effect=_make_paginator(ori_handler, all_keys)
        ):
            result = ori_handler.list_tickets(status="open", limit=5)

        assert len(result) == 5
        assert ori_handler.last_list_has_more is True  # 5 of 20

    def test_has_more_false_when_exhausted(self, ori_handler):
        all_keys = _keys(ori_handler, 1, 4)  # only 3 tickets total
        with patch.object(
            ori_handler, "_make_api_request", side_effect=_make_paginator(ori_handler, all_keys)
        ):
            result = ori_handler.list_tickets(status="open", limit=20)

        assert len(result) == 3
        assert ori_handler.last_list_has_more is False
        assert ori_handler.last_list_total == 3

    def test_total_is_real_total_from_backend(self, ori_handler):
        """``allReqFilter`` reports ``totalResults``; we surface it verbatim.

        Mirrors live ORI: one 20-row server page, but ``totalResults`` says
        204 — so ``total`` is the real 204 (not the 20 we fetched) and the
        capped result is flagged ``has_more`` (issue #94, replacing the old
        ``total is None`` behaviour).
        """
        page1 = _keys(ori_handler, 1, 21)  # 20 rows
        backend = _make_page_ignoring_backend(ori_handler, page1, total=204, total_pages=11)
        with patch.object(ori_handler, "_make_api_request", side_effect=backend):
            ori_handler.list_tickets(status="open", limit=20)

        assert ori_handler.last_list_total == 204
        assert ori_handler.last_list_has_more is True

    def test_page_ignoring_backend_not_reported_complete(self, ori_handler):
        """Regression for the #94 blocker.

        If the backend ignores ``selectedPage`` and re-serves the same first
        page, the walk must STOP (not loop) and the capped result must NOT be
        flagged ``has_more=False`` — that false "complete set" signal is the
        exact silent truncation #93 set out to fix. The real ``totalResults``
        keeps the signal honest.
        """
        page1 = _keys(ori_handler, 1, 21)  # 20 rows, served for every selectedPage
        backend = _make_page_ignoring_backend(ori_handler, page1, total=204, total_pages=11)
        with patch.object(ori_handler, "_make_api_request", side_effect=backend) as mock_api:
            result = ori_handler.list_tickets(status="open", limit=50)

        assert len(result) == 20  # only the one reachable server page
        assert ori_handler.last_list_has_more is True  # NOT a false "complete"
        assert ori_handler.last_list_total == 204
        assert mock_api.call_count == 2  # stopped on the duplicate page, no infinite loop

    def test_pagination_reuses_session_no_reauth(self, ori_handler):
        """Walking pages must not force a browser re-auth between pages (#93)."""
        all_keys = _keys(ori_handler, 1, 51)  # 50 tickets → 3 pages
        with (
            patch.object(
                ori_handler, "_make_api_request", side_effect=_make_paginator(ori_handler, all_keys)
            ),
            patch.object(ori_handler, "_guarded_authenticate") as mock_auth,
            patch.object(ori_handler, "_authenticate_with_browser") as mock_browser_auth,
        ):
            ori_handler.list_tickets(status="open", limit=50)

        mock_auth.assert_not_called()
        mock_browser_auth.assert_not_called()

    def test_first_page_api_failure_falls_back_to_html(self, ori_handler):
        """When the REST API yields nothing on page 1, fall back to HTML scraping
        and still report ``has_more`` from the fetched rows."""
        fallback_rows = [
            {
                "id": key,
                "summary": "x",
                "status": "Open",
                "created": "Unknown",
                "assignee": "Unknown",
                "url": f"https://example/{key}",
            }
            for key in _keys(ori_handler, 1, 22)  # 21 rows
        ]
        with (
            patch.object(ori_handler, "_make_api_request", return_value=None),
            patch.object(
                ori_handler, "_list_requests_html", return_value=fallback_rows
            ) as mock_html,
        ):
            result = ori_handler.list_tickets(status="open", limit=20)

        assert len(result) == 20
        assert ori_handler.last_list_has_more is True
        mock_html.assert_called_once()


# ── CLI more-signal surfacing ────────────────────────────────────────


_SAMPLE_TICKETS = [{"id": "SUPP-1", "summary": "first", "status": "Open"}]


def _cli_mock_handler(tickets, *, has_more, total=None):
    handler = MagicMock()
    handler.list_tickets.return_value = tickets
    handler.last_error = None
    handler.last_list_has_more = has_more
    handler.last_list_total = total
    # CLI + MCP now read the more-signal through this single accessor.
    handler.list_more_signal.return_value = {"has_more": has_more, "total": total}
    return handler


@pytest.mark.unit
class TestTicketsCliMoreSignal:
    """``dc-support-cli tickets`` exposes has_more/total (JSON) + a text hint."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_json_includes_has_more_and_total(self, mock_get):
        mock_get.return_value = _cli_mock_handler(_SAMPLE_TICKETS, has_more=True, total=None)
        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--limit", "1", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tickets"] == _SAMPLE_TICKETS
        assert data["count"] == 1
        assert data["has_more"] is True
        assert data["total"] is None

    @patch("dc_support_mcp.cli._get_handler")
    def test_json_total_when_backend_provides_one(self, mock_get):
        mock_get.return_value = _cli_mock_handler(_SAMPLE_TICKETS, has_more=True, total=42)
        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--limit", "1", "--json"])

        data = json.loads(result.output)
        assert data["total"] == 42

    @patch("dc_support_mcp.cli._get_handler")
    def test_text_shows_truncation_hint_when_capped(self, mock_get):
        mock_get.return_value = _cli_mock_handler(_SAMPLE_TICKETS, has_more=True)
        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--limit", "1"])

        assert result.exit_code == 0, result.output
        assert "more available" in result.output.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_text_no_hint_when_not_truncated(self, mock_get):
        mock_get.return_value = _cli_mock_handler(_SAMPLE_TICKETS, has_more=False)
        result = runner.invoke(app, ["tickets", "--vendor", "ori"])

        assert result.exit_code == 0, result.output
        assert "more available" not in result.output.lower()


# ── MCP tool more-signal surfacing ───────────────────────────────────


@pytest.mark.unit
class TestListVendorTicketsMcpMoreSignal:
    """The ``list_vendor_tickets`` MCP tool returns has_more/total."""

    def test_includes_has_more_and_total(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        handler = _cli_mock_handler(
            [{"id": "SUPP-1", "summary": "first", "status": "Open", "url": "x"}],
            has_more=True,
            total=None,
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=handler):
            result = list_vendor_tickets(vendor="ori", status="open", limit=1)

        assert "error" not in result
        assert result["count"] == 1
        assert result["has_more"] is True
        assert result["total"] is None
