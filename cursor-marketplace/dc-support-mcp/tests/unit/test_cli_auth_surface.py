"""Tests for the CLI auth-aware failure surface (issue #87).

The handler stores the real failure reason in ``handler.last_error``.
When that error looks like an auth / cooldown / login problem, the CLI
must print an auth-flavored message and exit with code 2 instead of the
generic "not found"-style exit 1.  Non-auth ``last_error`` values are
surfaced too, but with exit code 1.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dc_support_mcp.cli import _is_auth_error, app
from dc_support_mcp.constants import AUTH_COOLDOWN, COOKIE_MAX_AGE

runner = CliRunner()


def _fake_atlassian_handler(
    *,
    cookie_file=None,
    cookie_exists=True,
    cookie_age=timedelta(hours=1),
    probe_ok=True,
    cooldown_active=False,
    last_error=None,
):
    """Build a MagicMock that mimics an Atlassian handler for ``auth-status``."""
    handler = MagicMock()
    handler.cookie_file = cookie_file or MagicMock()
    handler.cookie_file.exists.return_value = cookie_exists
    stat = MagicMock()
    stat.st_mtime = (datetime.now() - cookie_age).timestamp()
    handler.cookie_file.stat.return_value = stat
    handler.cookie_file.__str__.return_value = "/tmp/fake_cookies.pkl"
    handler._probe_session = MagicMock(return_value=probe_ok)
    if cooldown_active:
        handler._last_auth_attempt = datetime.now()
        handler._last_auth_succeeded = False
    else:
        handler._last_auth_attempt = None
        handler._last_auth_succeeded = True
    handler.last_error = last_error

    # Issue #90: the CLI now delegates session-timing math to the handler.
    handler.cookie_age_seconds = MagicMock(
        return_value=int(cookie_age.total_seconds()) if cookie_exists else None
    )
    if cooldown_active:
        handler.cooldown_remaining_seconds = MagicMock(
            return_value=int(AUTH_COOLDOWN.total_seconds())
        )
    else:
        handler.cooldown_remaining_seconds = MagicMock(return_value=0)
    return handler


@pytest.mark.unit
class TestIsAuthError:
    """Substring detection used by the CLI failure helper."""

    @pytest.mark.parametrize(
        "message",
        [
            "Auth cooldown active (250s remaining)",
            "AUTH COOLDOWN ACTIVE",
            "auth cooldown active",
            "Browser login failed: timeout",
            "Login failed",
            "Cooldown active for 5 minutes",
            "auth failure for ori",
        ],
    )
    def test_recognises_auth_flavored_messages(self, message):
        assert _is_auth_error(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "HTTP 500: Internal Server Error",
            "Connection refused",
            "Ticket not found",
            "Bad gateway",
            "",
            None,
        ],
    )
    def test_rejects_non_auth_messages(self, message):
        assert _is_auth_error(message) is False

    def test_rejects_non_string(self):
        assert _is_auth_error(MagicMock()) is False


@pytest.mark.unit
class TestGetTicketAuthSurface:
    """``get-ticket`` surfaces ``handler.last_error`` instead of "not found"."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = (
            "Auth cooldown active (287s remaining). "
            "Skipping browser login to prevent account lockout."
        )
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1779", "--vendor", "ori"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "not found" not in combined.lower()
        assert "auth" in combined.lower()
        assert "ori" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_json_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = "Auth cooldown active (200s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1779", "--vendor", "ori", "--json"])

        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert "error" in data
        assert "auth" in data["error"].lower()
        assert "not found" not in data["error"].lower()
        assert data.get("vendor") == "ori"

    @patch("dc_support_mcp.cli._get_handler")
    def test_browser_login_failure_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = "Browser login failed: form timeout"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1234", "--vendor", "ori"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower() or "login" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_real_not_found_exits_1(self, mock_get_handler):
        """No ``last_error`` set → genuine 404, keep "not found" exit 1."""
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1779", "--vendor", "ori"])

        assert result.exit_code == 1, result.output
        combined = result.output + (result.stderr or "")
        assert "not found" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_non_auth_error_exits_1_with_detail(self, mock_get_handler):
        """Non-auth ``last_error`` is surfaced too, but with exit 1."""
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = "HTTP 500: Internal Server Error"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1779", "--vendor", "ori"])

        assert result.exit_code == 1, result.output
        combined = result.output + (result.stderr or "")
        assert "HTTP 500" in combined

    @patch("dc_support_mcp.cli._get_handler")
    def test_non_auth_error_json_includes_detail(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = "HTTP 500: Internal Server Error"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-1779", "--vendor", "ori", "--json"])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert "error" in data
        assert data.get("detail") == "HTTP 500: Internal Server Error"


@pytest.mark.unit
class TestTicketsAuthSurface:
    """``tickets`` surfaces ``handler.last_error`` when listing fails."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_handler.last_error = "Auth cooldown active (200s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()
        assert "no tickets found" not in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_json_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_handler.last_error = "Auth cooldown active"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--json"])

        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert "auth" in data["error"].lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_legitimately_empty_still_exits_0(self, mock_get_handler):
        """Empty list with no error is still treated as a real empty result."""
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori"])

        assert result.exit_code == 0, result.output
        assert "No tickets found" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_non_auth_error_exits_1_not_empty(self, mock_get_handler):
        """Empty list with a non-auth ``last_error`` is a real failure, not an empty result.

        The pre-check should treat any ``last_error`` as a failure (not just
        auth-flavored ones) — symmetric with the other six commands.
        """
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_handler.last_error = "HTTP 500: Internal Server Error"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori"])

        assert result.exit_code == 1, result.output
        combined = result.output + (result.stderr or "")
        assert "Failed to list tickets" in combined
        assert "HTTP 500" in combined
        assert "no tickets found" not in combined.lower()


@pytest.mark.unit
class TestCommentAuthSurface:
    """``comment`` surfaces ``handler.last_error``."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.add_comment.return_value = None
        mock_handler.last_error = "Auth cooldown active (180s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["comment", "SUPP-1234", "--vendor", "ori", "--text", "hi"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_real_failure_keeps_exit_1(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.add_comment.return_value = None
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["comment", "SUPP-1234", "--vendor", "ori", "--text", "hi"])

        assert result.exit_code == 1, result.output
        combined = result.output + (result.stderr or "")
        assert "failed to add comment" in combined.lower()


@pytest.mark.unit
class TestUpdateTicketAuthSurface:
    """``update-ticket`` surfaces ``handler.last_error``."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.update_ticket_status.return_value = None
        mock_handler.last_error = "Auth cooldown active (250s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "update-ticket",
                "SUPP-1234",
                "--vendor",
                "ori",
                "--status",
                "resolved",
            ],
        )

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()


@pytest.mark.unit
class TestKbSearchAuthSurface:
    """``kb-search`` surfaces ``handler.last_error`` when search fails."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.search_knowledge_base.return_value = None
        mock_handler.last_error = "Auth cooldown active (150s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-search", "power distribution", "--vendor", "iren"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_real_failure_keeps_exit_1(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.search_knowledge_base.return_value = None
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-search", "anything", "--vendor", "iren"])

        assert result.exit_code == 1, result.output


@pytest.mark.unit
class TestKbArticleAuthSurface:
    """``kb-article`` surfaces ``handler.last_error``."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = None
        mock_handler.last_error = "Login failed: bad creds"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-article", "12345", "--vendor", "iren"])

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower() or "login" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_real_not_found_keeps_exit_1(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = None
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-article", "12345", "--vendor", "iren"])

        assert result.exit_code == 1, result.output
        combined = result.output + (result.stderr or "")
        assert "not found" in combined.lower()


@pytest.mark.unit
class TestCreateServiceRequestAuthSurface:
    """``create-service-request`` distinguishes auth failures from other errors."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_exits_2(self, mock_get_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = None
        mock_handler.last_error = "Auth cooldown active (240s remaining)"
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "desc",
                "--vendor",
                "ori",
            ],
        )

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_auth_cooldown_json_exits_2(self, mock_get_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = None
        mock_handler.last_error = "Auth cooldown active"
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "desc",
                "--vendor",
                "ori",
                "--json",
            ],
        )

        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert "auth" in data["error"].lower()

    @patch("dc_support_mcp.cli._get_handler")
    def test_iren_auth_cooldown_exits_2(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = None
        mock_handler.last_error = "Auth cooldown active (200s remaining)"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "desc",
                "--vendor",
                "iren",
            ],
        )

        assert result.exit_code == 2, result.output
        combined = result.output + (result.stderr or "")
        assert "auth" in combined.lower()


# ── auth-status command ─────────────────────────────────────────────


@pytest.mark.unit
class TestAuthStatusCommand:
    """``dc-support-cli auth-status --vendor <v>`` reports session state."""

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_healthy_session_exits_0(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(hours=1),
            probe_ok=True,
            cooldown_active=False,
            last_error=None,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori"])

        assert result.exit_code == 0, result.output
        assert "ori" in result.output.lower()

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_no_cookie_file_exits_1(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=False,
            probe_ok=False,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori"])

        assert result.exit_code == 1, result.output

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_expired_cookies_exits_1(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=COOKIE_MAX_AGE + timedelta(minutes=10),
            probe_ok=False,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori"])

        assert result.exit_code == 1, result.output

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_probe_failure_exits_1(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(hours=1),
            probe_ok=False,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori"])

        assert result.exit_code == 1, result.output

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_active_cooldown_exits_1(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(hours=1),
            probe_ok=True,
            cooldown_active=True,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori"])

        assert result.exit_code == 1, result.output
        assert "cooldown" in result.output.lower()

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_json_output_shape(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(minutes=30),
            probe_ok=True,
            cooldown_active=False,
            last_error=None,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for key in (
            "vendor",
            "cookie_exists",
            "cookie_age_seconds",
            "cookie_max_age_seconds",
            "cookie_fresh",
            "cooldown_remaining_seconds",
            "probe_supported",
            "probe_ok",
            "last_error",
            "usable",
        ):
            assert key in data, f"missing key: {key}"
        assert data["vendor"] == "ori"
        assert data["usable"] is True
        assert data["cookie_exists"] is True
        assert data["probe_ok"] is True
        assert data["cooldown_remaining_seconds"] == 0
        assert data["last_error"] is None

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_json_cooldown_reports_remaining_seconds(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(hours=1),
            probe_ok=True,
            cooldown_active=True,
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori", "--json"])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert 0 < data["cooldown_remaining_seconds"] <= int(AUTH_COOLDOWN.total_seconds())

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_json_includes_last_error(self, mock_build):
        mock_build.return_value = _fake_atlassian_handler(
            cookie_exists=True,
            cookie_age=timedelta(hours=1),
            probe_ok=False,
            last_error="Browser login failed: form timeout",
        )

        result = runner.invoke(app, ["auth-status", "--vendor", "ori", "--json"])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["last_error"] == "Browser login failed: form timeout"
        assert data["usable"] is False

    @patch("dc_support_mcp.cli._build_inspection_handler")
    def test_iren_without_probe_uses_cookie_state(self, mock_build):
        """IREN handler doesn't expose ``_probe_session``."""
        handler = MagicMock(spec=[])
        handler.cookie_file = MagicMock()
        handler.cookie_file.exists.return_value = True
        stat = MagicMock()
        stat.st_mtime = (datetime.now() - timedelta(hours=1)).timestamp()
        handler.cookie_file.stat.return_value = stat
        handler.cookie_file.__str__.return_value = "/tmp/iren_cookies.pkl"
        handler._last_auth_attempt = None
        handler._last_auth_succeeded = True
        handler.last_error = None
        handler.cookie_age_seconds = MagicMock(return_value=int(timedelta(hours=1).total_seconds()))
        handler.cooldown_remaining_seconds = MagicMock(return_value=0)
        mock_build.return_value = handler

        result = runner.invoke(app, ["auth-status", "--vendor", "iren", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["vendor"] == "iren"
        assert data["probe_supported"] is False
        assert data["probe_ok"] is None
        assert data["usable"] is True
