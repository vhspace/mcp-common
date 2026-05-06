"""Unit tests for add_comment, list_tickets, CLI, and MCP tool wrappers."""

import json
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dc_support_mcp.cli import app
from dc_support_mcp.vendors.ori import OriVendorHandler

runner = CliRunner()


@pytest.mark.unit
class TestAddComment:
    def test_add_comment_success(self, ori_handler):
        with patch.object(ori_handler.session, "post") as mock_post:
            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {
                "id": "12345",
                "body": "Test comment",
            }

            result = ori_handler.add_comment("SUPP-1552", "Test comment", public=True)

            assert result is not None
            assert "id" in result

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["body"] == "Test comment"
            assert call_kwargs["json"]["public"] is True

    def test_add_comment_with_session_refresh(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        resp_401 = type("Response", (), {"status_code": 401})()
        resp_201 = type("Response", (), {"status_code": 201})()
        resp_201.json = lambda self=None: {"id": "12345", "body": "Test"}

        with patch.object(handler.session, "post") as mock_post:
            mock_post.side_effect = [resp_401, resp_201]

            with patch.object(handler, "_authenticate_with_browser"):
                handler.add_comment("SUPP-1552", "Test", public=True)

    def test_add_comment_internal(self, ori_handler):
        with patch.object(ori_handler.session, "post") as mock_post:
            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {"id": "12345"}

            ori_handler.add_comment("SUPP-1552", "Internal note", public=False)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["public"] is False

    def test_add_comment_failure(self, ori_handler):
        with patch.object(ori_handler.session, "post") as mock_post:
            mock_post.return_value.status_code = 500
            mock_post.return_value.text = "Internal Server Error"
            result = ori_handler.add_comment("SUPP-1552", "Test", public=True)
            assert result is None


@pytest.mark.unit
class TestListRequests:
    def test_list_tickets_via_handler(self, ori_handler):
        assert hasattr(ori_handler, "list_tickets")
        assert callable(ori_handler.list_tickets)


@pytest.mark.unit
class TestMCPTools:
    """Test the MCP server tool functions."""

    def test_create_vendor_ticket_unknown_vendor_raises(self):
        from fastmcp.exceptions import ToolError

        from dc_support_mcp.mcp_server import create_vendor_ticket

        with pytest.raises(ToolError, match="not registered"):
            create_vendor_ticket(
                summary="test",
                description="test",
                cause="test",
                vendor="nonexistent",
            )

    def test_list_vendor_tickets_unknown_vendor_raises(self):
        from fastmcp.exceptions import ToolError

        from dc_support_mcp.mcp_server import list_vendor_tickets

        with pytest.raises(ToolError, match="not registered"):
            list_vendor_tickets(vendor="nonexistent")

    def test_get_vendor_ticket_unknown_vendor_raises(self):
        from fastmcp.exceptions import ToolError

        from dc_support_mcp.mcp_server import get_vendor_ticket

        with pytest.raises(ToolError, match="not registered"):
            get_vendor_ticket(ticket_id="SUPP-1234", vendor="nonexistent")


@pytest.mark.unit
class TestCLIJsonEmptyOutput:
    """Regression tests for --json flag with empty results (issue #17)."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_tickets_json_empty_result(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"tickets": [], "count": 0}

    @patch("dc_support_mcp.cli._get_handler")
    def test_tickets_json_empty_result_none(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"tickets": [], "count": 0}

    @patch("dc_support_mcp.cli._get_handler")
    def test_tickets_no_json_empty_result(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["tickets", "--vendor", "ori"])
        assert result.exit_code == 0
        assert "No tickets found" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_get_ticket_json_not_found(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["get-ticket", "SUPP-9999", "--vendor", "ori", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "SUPP-9999" in data["error"]


@pytest.mark.unit
class TestUpdateVendorTicketStatus:
    """Tests for update_vendor_ticket_status MCP tool."""

    def test_unknown_status_returns_error(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        result = update_vendor_ticket_status(ticket_id="SUPP-1234", status="invalid", vendor="ori")
        assert "error" in result
        assert "Unknown status" in result["error"]

    def test_unknown_vendor_raises(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        with pytest.raises(ValueError, match="not registered"):
            update_vendor_ticket_status(ticket_id="123", status="resolved", vendor="nonexistent")

    def test_ori_calls_handler_with_status_string(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.update_ticket_status.return_value = {
                "ok": True,
                "ticket_id": "SUPP-1234",
                "new_status": "Resolve this issue",
            }
            mock_get.return_value = mock_handler

            result = update_vendor_ticket_status(
                ticket_id="SUPP-1234", status="resolved", vendor="ori"
            )
            assert result["ok"] is True
            mock_handler.update_ticket_status.assert_called_once_with("SUPP-1234", "resolved")

    def test_iren_calls_handler_with_status_code(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.update_ticket_status.return_value = {
                "ok": True,
                "ticket_id": "12345",
                "new_status": "Resolved",
            }
            mock_get.return_value = mock_handler

            result = update_vendor_ticket_status(
                ticket_id="12345", status="resolved", vendor="iren"
            )
            assert result["ok"] is True
            mock_handler.update_ticket_status.assert_called_once_with("12345", 4)


@pytest.mark.unit
class TestUpdateTicketCLI:
    """Tests for update-ticket CLI command (issue #20)."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_update_ticket_ori_resolved(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.update_ticket_status.return_value = {
            "ok": True,
            "ticket_id": "SUPP-1668",
            "new_status": "Resolve this issue",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["update-ticket", "SUPP-1668", "--vendor", "ori", "--status", "resolved", "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        mock_handler.update_ticket_status.assert_called_once_with("SUPP-1668", "resolved")

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_update_ticket_ori_closed(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.update_ticket_status.return_value = {
            "ok": True,
            "ticket_id": "SUPP-1669",
            "new_status": "Close",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["update-ticket", "SUPP-1669", "--vendor", "ori", "--status", "closed"]
        )
        assert result.exit_code == 0, result.output
        assert "ok" in result.output
        mock_handler.update_ticket_status.assert_called_once_with("SUPP-1669", "closed")

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_update_ticket_iren_uses_status_code(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.update_ticket_status.return_value = {
            "ok": True,
            "ticket_id": "12345",
            "new_status": "Resolved",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["update-ticket", "12345", "--vendor", "iren", "--status", "resolved", "--json"]
        )
        assert result.exit_code == 0, result.output
        mock_handler.update_ticket_status.assert_called_once_with("12345", 4)

    def test_cli_update_ticket_invalid_status(self):
        result = runner.invoke(
            app, ["update-ticket", "SUPP-1668", "--vendor", "ori", "--status", "invalid"]
        )
        assert result.exit_code == 1
        assert "Unknown status" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_update_ticket_handler_failure(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.update_ticket_status.return_value = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["update-ticket", "SUPP-1668", "--vendor", "ori", "--status", "resolved"]
        )
        assert result.exit_code == 1
        assert "Failed to update" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_update_ticket_unsupported_vendor(self, mock_get_handler):
        mock_handler = MagicMock(spec=[])
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app, ["update-ticket", "SUPP-1668", "--vendor", "ori", "--status", "resolved"]
        )
        assert result.exit_code == 1
        assert "does not support" in result.output


@pytest.mark.unit
class TestOriTransitions:
    """Tests for ORI Atlassian Service Desk ticket transitions (issue #20)."""

    def test_update_ticket_status_resolved(self, ori_handler):
        with (
            patch.object(ori_handler.session, "get") as mock_get,
            patch.object(ori_handler.session, "post") as mock_post,
        ):
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request/SUPP-1668/transition"
            mock_get_resp.json.return_value = {
                "values": [
                    {"id": "21", "name": "Resolve this issue"},
                    {"id": "31", "name": "Close"},
                ]
            }
            mock_get.return_value = mock_get_resp

            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 204
            mock_post_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request/SUPP-1668/transition"
            mock_post.return_value = mock_post_resp

            result = ori_handler.update_ticket_status("SUPP-1668", "resolved")
            assert result is not None
            assert result["ok"] is True
            assert result["ticket_id"] == "SUPP-1668"
            assert "Resolve" in result["new_status"]

    def test_update_ticket_status_closed(self, ori_handler):
        with (
            patch.object(ori_handler.session, "get") as mock_get,
            patch.object(ori_handler.session, "post") as mock_post,
        ):
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request/SUPP-1669/transition"
            mock_get_resp.json.return_value = {
                "values": [
                    {"id": "21", "name": "Resolve this issue"},
                    {"id": "31", "name": "Close"},
                ]
            }
            mock_get.return_value = mock_get_resp

            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_post_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request/SUPP-1669/transition"
            mock_post.return_value = mock_post_resp

            result = ori_handler.update_ticket_status("SUPP-1669", "closed")
            assert result is not None
            assert result["ok"] is True
            assert "Close" in result["new_status"]

    def test_update_ticket_status_no_matching_transition(self, ori_handler):
        with patch.object(ori_handler.session, "get") as mock_get:
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request/SUPP-1670/transition"
            mock_get_resp.json.return_value = {
                "values": [{"id": "11", "name": "Waiting for customer"}]
            }
            mock_get.return_value = mock_get_resp

            result = ori_handler.update_ticket_status("SUPP-1670", "resolved")
            assert result is None

    def test_update_ticket_status_invalid_ticket_id(self, ori_handler):
        with pytest.raises(ValueError, match="Invalid ticket ID"):
            ori_handler.update_ticket_status("INVALID-123", "resolved")

    def test_mcp_tool_ori_resolved(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.update_ticket_status.return_value = {
                "ok": True,
                "ticket_id": "SUPP-1668",
                "new_status": "Resolve this issue",
            }
            mock_get.return_value = mock_handler

            result = update_vendor_ticket_status(
                ticket_id="SUPP-1668", status="resolved", vendor="ori"
            )
            assert result["ok"] is True
            mock_handler.update_ticket_status.assert_called_once_with("SUPP-1668", "resolved")


@pytest.mark.unit
class TestCreateServiceRequestErrorDetail:
    """Regression tests for create-service-request error detail (issue #35)."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_shows_error_detail_text(self, mock_get_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = None
        mock_handler.last_error = "HTTP 400: Bad Request - invalid field"
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "test desc",
                "--vendor",
                "ori",
            ],
        )
        assert result.exit_code == 1
        assert "HTTP 400" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_shows_error_detail_json(self, mock_get_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = None
        mock_handler.last_error = "HTTP 400: Bad Request"
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "test desc",
                "--vendor",
                "ori",
                "--json",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "detail" in data
        assert "HTTP 400" in data["detail"]

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_fallback_when_no_last_error(self, mock_get_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = None
        mock_handler.last_error = None
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary",
                "test",
                "--description",
                "test desc",
                "--vendor",
                "ori",
                "--json",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "Unknown error" in data["detail"]

    def test_base_handler_stores_last_error_on_http_failure(self, ori_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        with patch.object(ori_handler.session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = '{"error": "Bad Request", "message": "Invalid field"}'
            mock_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request"
            mock_post.return_value = mock_resp

            result = AtlassianServiceDeskHandler.create_service_desk_request(
                ori_handler, summary="test", description="test"
            )
            assert result is None
            assert ori_handler.last_error is not None
            assert "400" in ori_handler.last_error

    def test_base_handler_clears_last_error_on_success(self, ori_handler):
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        ori_handler.last_error = "previous error"
        with patch.object(ori_handler.session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            mock_resp.url = "https://oriindustries.atlassian.net/rest/servicedeskapi/request"
            mock_resp.json.return_value = {"issueKey": "SUPP-9999", "issueId": "12345"}
            mock_post.return_value = mock_resp

            result = AtlassianServiceDeskHandler.create_service_desk_request(
                ori_handler, summary="test", description="test"
            )
            assert result is not None
            assert ori_handler.last_error is None

    def test_ori_create_ticket_stores_last_error(self, ori_handler):
        with patch.object(ori_handler, "create_ticket", return_value=None):
            ori_handler.last_error = "TimeoutError: form submission timed out"
            result = ori_handler.create_service_desk_request(summary="test", description="test")
            assert result is None
            assert "TimeoutError" in ori_handler.last_error


@pytest.mark.unit
class TestCreateServiceRequestIren:
    """Tests for create-service-request with --vendor iren (issue #71)."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_success_json(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = {
            "id": "42",
            "summary": "GPU down on dfw01-cpu-04",
            "url": "https://support.iren.com/support/tickets/42",
            "status": "Open",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "GPU down on dfw01-cpu-04",
                "--description", "Node won't boot after power cycle",
                "--vendor", "iren",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["ticket_id"] == "42"
        assert data["vendor"] == "iren"
        assert "support.iren.com" in data["url"]
        mock_handler.create_ticket.assert_called_once_with(
            summary="GPU down on dfw01-cpu-04",
            description="Node won't boot after power cycle",
            priority="P3",
        )

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_success_text(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = {
            "id": "100",
            "summary": "test",
            "url": "https://support.iren.com/support/tickets/100",
            "status": "Open",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "test",
                "--description", "desc",
                "--vendor", "iren",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ok" in result.output
        assert "100" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_custom_priority(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = {
            "id": "55",
            "summary": "urgent",
            "url": "https://support.iren.com/support/tickets/55",
            "status": "Open",
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "urgent issue",
                "--description", "critical failure",
                "--vendor", "iren",
                "--priority", "P1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        mock_handler.create_ticket.assert_called_once_with(
            summary="urgent issue",
            description="critical failure",
            priority="P1",
        )

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_failure_text(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = None
        mock_handler.last_error = "Freshdesk API error: 422"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "test",
                "--description", "desc",
                "--vendor", "iren",
            ],
        )
        assert result.exit_code == 1
        assert "Failed to create IREN ticket" in result.output
        assert "422" in result.output

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_failure_json(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = None
        mock_handler.last_error = "Auth failed"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "test",
                "--description", "desc",
                "--vendor", "iren",
                "--json",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "IREN" in data["error"]
        assert "Auth failed" in data["detail"]

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_iren_failure_no_last_error(self, mock_get_handler):
        from dc_support_mcp.vendors.iren import IrenVendorHandler

        mock_handler = MagicMock(spec=IrenVendorHandler)
        mock_handler.create_ticket.return_value = None
        mock_handler.last_error = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "test",
                "--description", "desc",
                "--vendor", "iren",
                "--json",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "Unknown error" in data["detail"]

    @patch("dc_support_mcp.cli._get_handler")
    def test_cli_ori_still_works(self, mock_get_handler):
        """Existing Atlassian vendors are unaffected by IREN addition."""
        from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler

        mock_handler = MagicMock(spec=AtlassianServiceDeskHandler)
        mock_handler.create_service_desk_request.return_value = {
            "issueKey": "SUPP-5000",
            "issueId": "12345",
        }
        mock_handler.INFRA_REQUEST_TYPE_ID = "7"
        mock_handler.BASE_URL = "https://oriindustries.atlassian.net"
        mock_handler.PORTAL_ID = "3"
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(
            app,
            [
                "create-service-request",
                "--summary", "test",
                "--description", "desc",
                "--vendor", "ori",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["ticket_id"] == "SUPP-5000"
        assert data["vendor"] == "ori"


@pytest.mark.unit
class TestCreateServiceRequestIrenMCP:
    """Tests for create_vendor_service_request MCP tool with IREN (issue #71)."""

    def test_mcp_iren_success(self):
        from dc_support_mcp.mcp_server import create_vendor_service_request

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            from dc_support_mcp.vendors.iren import IrenVendorHandler

            mock_handler = MagicMock(spec=IrenVendorHandler)
            mock_handler.create_ticket.return_value = {
                "id": "77",
                "summary": "GPU down",
                "url": "https://support.iren.com/support/tickets/77",
                "status": "Open",
            }
            mock_get.return_value = mock_handler

            result = create_vendor_service_request(
                summary="GPU down",
                description="Node won't boot",
                vendor="iren",
                priority="P2",
            )

        assert result["ok"] is True
        assert result["ticket_id"] == "77"
        assert result["vendor"] == "iren"
        mock_handler.create_ticket.assert_called_once_with(
            summary="GPU down",
            description="Node won't boot",
            priority="P2",
        )

    def test_mcp_iren_failure(self):
        from dc_support_mcp.mcp_server import create_vendor_service_request

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            from dc_support_mcp.vendors.iren import IrenVendorHandler

            mock_handler = MagicMock(spec=IrenVendorHandler)
            mock_handler.create_ticket.return_value = None
            mock_handler.last_error = None
            mock_get.return_value = mock_handler

            result = create_vendor_service_request(
                summary="test",
                description="desc",
                vendor="iren",
            )

        assert "error" in result
        assert "IREN" in result["error"]


@pytest.mark.unit
class TestTriageAssignee:
    """Tests for --assignee / assignee parameter in triage (issue #19)."""

    RTB_DEVICE_RESP: ClassVar[dict] = {"id": 42, "name": "us-south-3a-r07-06"}
    RTB_TRIAGE_RESP: ClassVar[dict] = {
        "ticket": {"id": "SRE-100", "title": "GPU triage", "url": "https://linear.app/SRE-100"},
        "netbox_updated": True,
    }

    def _mock_rtb(self, responses_mock):
        """Register RTB device + triage endpoint mocks."""
        import responses

        responses_mock.add(
            responses.GET,
            "https://rtb.together.ai/api/v1/device/us-south-3a-r07-06",
            json=self.RTB_DEVICE_RESP,
            status=200,
        )
        responses_mock.add(
            responses.POST,
            "https://rtb.together.ai/api/v1/tickets/triage",
            json=self.RTB_TRIAGE_RESP,
            status=201,
        )

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.oncall.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.oncall.get_oncall_email", return_value="oncall@together.ai")
    def test_cli_assignee_overrides_oncall(self, mock_oncall, mock_assign):
        import responses

        with responses.RequestsMock() as rsps:
            self._mock_rtb(rsps)
            result = runner.invoke(
                app,
                [
                    "triage",
                    "--device",
                    "us-south-3a-r07-06",
                    "--summary",
                    "GPU missing",
                    "--assignee",
                    "explicit@together.ai",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["assignee"] == "explicit@together.ai"
        mock_assign.assert_called_once_with("SRE-100", "explicit@together.ai")

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.oncall.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.oncall.get_oncall_email", return_value="oncall@together.ai")
    def test_cli_assignee_overrides_created_by(self, mock_oncall, mock_assign):
        import responses

        with responses.RequestsMock() as rsps:
            self._mock_rtb(rsps)
            result = runner.invoke(
                app,
                [
                    "triage",
                    "--device",
                    "us-south-3a-r07-06",
                    "--summary",
                    "GPU missing",
                    "--created-by",
                    "creator@together.ai",
                    "--assignee",
                    "explicit@together.ai",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["assignee"] == "explicit@together.ai"

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.oncall.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.oncall.get_oncall_email", return_value="oncall@together.ai")
    def test_cli_falls_back_to_created_by(self, mock_oncall, mock_assign):
        import responses

        with responses.RequestsMock() as rsps:
            self._mock_rtb(rsps)
            result = runner.invoke(
                app,
                [
                    "triage",
                    "--device",
                    "us-south-3a-r07-06",
                    "--summary",
                    "GPU missing",
                    "--created-by",
                    "creator@together.ai",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["assignee"] == "creator@together.ai"

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.oncall.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.oncall.get_oncall_email", return_value="oncall@together.ai")
    def test_cli_falls_back_to_oncall(self, mock_oncall, mock_assign):
        import responses

        with responses.RequestsMock() as rsps:
            self._mock_rtb(rsps)
            result = runner.invoke(
                app,
                [
                    "triage",
                    "--device",
                    "us-south-3a-r07-06",
                    "--summary",
                    "GPU missing",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["assignee"] == "oncall@together.ai"

    def test_build_payload_includes_assignee_email(self):
        from dc_support_mcp.formatting import build_rtb_triage_payload

        payload = build_rtb_triage_payload(
            device_id=42,
            issue_summary="GPU missing",
            issue_types=["GPU issue"],
            assignee_email="explicit@together.ai",
        )
        assert payload["assignee_email"] == "explicit@together.ai"

    def test_build_payload_omits_assignee_when_empty(self):
        from dc_support_mcp.formatting import build_rtb_triage_payload

        payload = build_rtb_triage_payload(
            device_id=42,
            issue_summary="GPU missing",
            issue_types=["GPU issue"],
            assignee_email="",
        )
        assert "assignee_email" not in payload

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.mcp_server.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.mcp_server.get_oncall_email", return_value="oncall@together.ai")
    def test_mcp_tool_assignee_overrides_oncall(self, mock_oncall, mock_assign):
        import responses

        from dc_support_mcp.mcp_server import create_rtb_triage_ticket

        with responses.RequestsMock() as rsps:
            self._mock_rtb(rsps)
            result = create_rtb_triage_ticket(
                device_name="us-south-3a-r07-06",
                issue_summary="GPU missing",
                assignee="explicit@together.ai",
            )
        assert result["ok"] is True
        assert result["assignee"] == "explicit@together.ai"
        mock_assign.assert_called_once_with("SRE-100", "explicit@together.ai")


@pytest.mark.unit
class TestRtbOutageTypeValidation:
    """Tests for RTB outage type enum and local validation (issue #39)."""

    def test_valid_outage_type_returns_canonical(self):
        from dc_support_mcp.validation import validate_gpu_outage_type

        assert validate_gpu_outage_type("GPU - Missing") == "GPU - Missing"

    def test_valid_outage_type_case_insensitive(self):
        from dc_support_mcp.validation import validate_gpu_outage_type

        assert validate_gpu_outage_type("gpu - missing") == "GPU - Missing"
        assert validate_gpu_outage_type("NCCL ERROR") == "NCCL Error"
        assert validate_gpu_outage_type("other") == "Other"

    def test_invalid_outage_type_raises(self):
        from dc_support_mcp.validation import ValidationError, validate_gpu_outage_type

        with pytest.raises(ValidationError, match="Invalid gpu_outage_type"):
            validate_gpu_outage_type("Bogus Type")

    def test_constants_has_24_types(self):
        from dc_support_mcp.constants import RTB_OUTAGE_TYPES

        assert len(RTB_OUTAGE_TYPES) == 24

    def test_cli_list_outage_types(self):
        result = runner.invoke(app, ["triage", "--list-outage-types"])
        assert result.exit_code == 0
        assert "GPU - Missing" in result.output
        assert "NCCL Error" in result.output

    def test_cli_list_outage_types_json(self):
        result = runner.invoke(app, ["triage", "--list-outage-types", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "outage_types" in data
        assert len(data["outage_types"]) == 24

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_cli_triage_rejects_invalid_outage_type(self):
        result = runner.invoke(
            app,
            [
                "triage",
                "--device",
                "test-node",
                "--summary",
                "test",
                "--gpu-outage-type",
                "Invalid Type",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid gpu_outage_type" in result.output

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_tool_rejects_invalid_outage_type(self):
        from dc_support_mcp.mcp_server import create_rtb_triage_ticket

        result = create_rtb_triage_ticket(
            device_name="test-node",
            issue_summary="test",
            gpu_outage_type="Invalid Type",
        )
        assert "error" in result
        assert "Invalid gpu_outage_type" in result["error"]

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    @patch("dc_support_mcp.oncall.linear_assign_ticket", return_value=True)
    @patch("dc_support_mcp.oncall.get_oncall_email", return_value="oncall@together.ai")
    def test_cli_triage_accepts_valid_outage_type(self, mock_oncall, mock_assign):
        import responses

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://rtb.together.ai/api/v1/device/us-south-3a-r07-06",
                json={"id": 42, "name": "us-south-3a-r07-06"},
                status=200,
            )
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/tickets/triage",
                json={
                    "ticket": {
                        "id": "SRE-200",
                        "title": "test",
                        "url": "https://linear.app/SRE-200",
                    },
                    "netbox_updated": True,
                },
                status=201,
            )
            result = runner.invoke(
                app,
                [
                    "triage",
                    "--device",
                    "us-south-3a-r07-06",
                    "--summary",
                    "test",
                    "--gpu-outage-type",
                    "NCCL Error",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output


@pytest.mark.unit
class TestSetNodeActive:
    """Tests for set_node_active MCP tool and set-active CLI command (issue #51)."""

    # ── MCP tool tests ──────────────────────────────────────────────────

    def test_mcp_missing_rtb_key(self):
        from dc_support_mcp.mcp_server import set_node_active

        with patch.dict("os.environ", {}, clear=True):
            result = set_node_active(device_name="us-south-3a-r07-06")
        assert "error" in result
        assert "RTB_API_KEY" in result["error"]

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_missing_both_identifiers(self):
        from dc_support_mcp.mcp_server import set_node_active

        result = set_node_active()
        assert "error" in result
        assert "device_name" in result["error"] or "resource_id" in result["error"]

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_set_active_by_name_success(self):
        import responses

        from dc_support_mcp.mcp_server import set_node_active

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/by-name/us-south-3a-r07-06/set-active",
                json={"success": True},
                status=200,
            )
            result = set_node_active(device_name="us-south-3a-r07-06")

        assert result["ok"] is True
        assert result["device_name"] == "us-south-3a-r07-06"

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_set_active_by_id_success(self):
        import responses

        from dc_support_mcp.mcp_server import set_node_active

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/device/1492/set-active",
                json={"success": True},
                status=200,
            )
            result = set_node_active(resource_id=1492, resource_type="device")

        assert result["ok"] is True
        assert "1492" in result["device_name"]

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_set_active_by_name_rtb_error(self):
        import responses

        from dc_support_mcp.mcp_server import set_node_active

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/by-name/bad-node/set-active",
                json={"success": False, "error": "device not found"},
                status=500,
            )
            result = set_node_active(device_name="bad-node")

        assert "error" in result
        assert "500" in result["error"]

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_mcp_invalid_resource_type(self):
        from dc_support_mcp.mcp_server import set_node_active

        result = set_node_active(resource_id=1492, resource_type="container")
        assert "error" in result
        assert "resource_type" in result["error"]

    # ── CLI tests ───────────────────────────────────────────────────────

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_cli_set_active_by_name_success(self):
        import responses

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/by-name/us-south-3a-r07-06/set-active",
                json={"success": True},
                status=200,
            )
            result = runner.invoke(
                app,
                ["set-active", "--device", "us-south-3a-r07-06", "--json"],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["device_name"] == "us-south-3a-r07-06"

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_cli_set_active_by_id_success(self):
        import responses

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/device/1492/set-active",
                json={"success": True},
                status=200,
            )
            result = runner.invoke(
                app,
                ["set-active", "--resource-id", "1492", "--resource-type", "device", "--json"],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_cli_missing_identifiers(self):
        with patch.dict("os.environ", {"RTB_API_KEY": "test-key"}):
            result = runner.invoke(app, ["set-active"])
        assert result.exit_code == 1
        assert "device" in result.output.lower() or "resource" in result.output.lower()

    @patch.dict("os.environ", {"RTB_API_KEY": "test-key"})
    def test_cli_rtb_error(self):
        import responses

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://rtb.together.ai/api/v1/nodes/by-name/bad-node/set-active",
                json={"success": False, "error": "not found"},
                status=500,
            )
            result = runner.invoke(
                app,
                ["set-active", "--device", "bad-node"],
            )
        assert result.exit_code == 1
        assert "500" in result.output

    def test_cli_missing_rtb_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["set-active", "--device", "some-node"])
        assert result.exit_code == 1
        assert "RTB_API_KEY" in result.output


@pytest.mark.unit
class TestKBArticleCLI:
    """Tests for kb-article CLI command with URL support and attachments (issue #34)."""

    @patch("dc_support_mcp.cli._get_handler")
    def test_kb_article_with_numeric_id(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = {
            "id": "42",
            "title": "Test Article",
            "url": "https://support.iren.com/support/solutions/articles/42",
            "category": None,
            "last_modified": None,
            "content": "Some content",
            "attachments": [],
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-article", "42", "--vendor", "iren", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "42"
        mock_handler.get_kb_article.assert_called_once_with("42")

    @patch("dc_support_mcp.cli._get_handler")
    def test_kb_article_with_url(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = {
            "id": "73000682456",
            "title": "From URL",
            "url": "https://support.iren.com/support/solutions/articles/73000682456",
            "category": None,
            "last_modified": None,
            "content": "Content",
            "attachments": [],
        }
        mock_get_handler.return_value = mock_handler

        url = "https://support.iren.com/support/solutions/articles/73000682456-some-title"
        result = runner.invoke(app, ["kb-article", url, "--vendor", "iren", "--json"])
        assert result.exit_code == 0
        mock_handler.get_kb_article.assert_called_once_with(url)

    @patch("dc_support_mcp.cli._get_handler")
    def test_kb_article_json_includes_attachments(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = {
            "id": "50",
            "title": "Article With Attachments",
            "url": "https://support.iren.com/support/solutions/articles/50",
            "category": "General / Guides",
            "last_modified": "2025-06-01",
            "content": "See attached PDF",
            "attachments": [
                {
                    "name": "guide.pdf",
                    "url": "https://support.iren.com/files/guide.pdf",
                    "content_type": "application/pdf",
                    "size": 2048,
                }
            ],
        }
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-article", "50", "--vendor", "iren", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["attachments"]) == 1
        assert data["attachments"][0]["name"] == "guide.pdf"
        assert data["attachments"][0]["size"] == 2048

    @patch("dc_support_mcp.cli._get_handler")
    def test_kb_article_not_found(self, mock_get_handler):
        mock_handler = MagicMock()
        mock_handler.get_kb_article.return_value = None
        mock_get_handler.return_value = mock_handler

        result = runner.invoke(app, ["kb-article", "99999", "--vendor", "iren"])
        assert result.exit_code == 1
        assert "not found" in result.output


@pytest.mark.unit
class TestKBArticleMCPTool:
    """Tests for get_vendor_kb_article MCP tool (issue #34)."""

    def test_accepts_url_argument(self):
        from dc_support_mcp.mcp_server import get_vendor_kb_article

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.get_kb_article.return_value = {
                "id": "42",
                "title": "Test",
                "url": "https://example.com/42",
                "category": None,
                "last_modified": None,
                "content": "body",
                "attachments": [],
            }
            mock_get.return_value = mock_handler

            url = "https://support.iren.com/support/solutions/articles/42-slug"
            result = get_vendor_kb_article(article_id=url, vendor="iren")
            assert result["id"] == "42"
            mock_handler.get_kb_article.assert_called_once_with(url)

    def test_returns_error_for_missing_article(self):
        from dc_support_mcp.mcp_server import get_vendor_kb_article

        with patch("dc_support_mcp.mcp_server._get_handler") as mock_get:
            mock_handler = MagicMock()
            mock_handler.get_kb_article.return_value = None
            mock_get.return_value = mock_handler

            result = get_vendor_kb_article(article_id="99999", vendor="iren")
            assert "error" in result
