"""Tests for dc_support_mcp.oncall — Linear assignment and listing."""

from unittest.mock import MagicMock, patch

import requests

from dc_support_mcp.oncall import (
    is_email,
    linear_assign_ticket,
)


class TestIsEmail:
    def test_valid_emails(self):
        assert is_email("user@together.ai")
        assert is_email("laura@together.ai")
        assert is_email("a+b@example.com")

    def test_invalid(self):
        assert not is_email("")
        assert not is_email("danil")
        assert not is_email("Placeholder")
        assert not is_email("not an email")
        assert not is_email("@missing.local")


class TestLinearAssignTicket:
    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test_key"})
    def test_successful_assignment(self, mock_post):
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {
            "data": {"users": {"nodes": [{"id": "user-uuid-123", "email": "sre@together.ai"}]}}
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {
            "data": {
                "issueUpdate": {
                    "success": True,
                    "issue": {"identifier": "SRE-1574", "assignee": {"email": "sre@together.ai"}},
                }
            }
        }

        mock_post.side_effect = [user_resp, update_resp]

        result = linear_assign_ticket("SRE-1574", "sre@together.ai")
        assert result is True
        assert mock_post.call_count == 2

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test_key"})
    def test_user_not_found(self, mock_post):
        user_resp = MagicMock()
        user_resp.status_code = 200
        user_resp.json.return_value = {"data": {"users": {"nodes": []}}}

        mock_post.return_value = user_resp

        result = linear_assign_ticket("SRE-1574", "unknown@together.ai")
        assert result is False
        assert mock_post.call_count == 1

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_false(self):
        result = linear_assign_ticket("SRE-1574", "sre@together.ai")
        assert result is False

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test_key"})
    def test_network_error_returns_false(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("timeout")

        result = linear_assign_ticket("SRE-1574", "sre@together.ai")
        assert result is False


class TestBuildRtbTriagePayloadAssignee:
    """Test that build_rtb_triage_payload includes assignee_email."""

    def test_payload_with_assignee(self):
        from dc_support_mcp.formatting import build_rtb_triage_payload

        payload = build_rtb_triage_payload(
            device_id=123,
            issue_summary="test",
            issue_types=["GPU issue"],
            assignee_email="sre@together.ai",
        )
        assert payload["assignee_email"] == "sre@together.ai"

    def test_payload_without_assignee(self):
        from dc_support_mcp.formatting import build_rtb_triage_payload

        payload = build_rtb_triage_payload(
            device_id=123,
            issue_summary="test",
            issue_types=["GPU issue"],
        )
        assert "assignee_email" not in payload

    def test_payload_empty_assignee_omitted(self):
        from dc_support_mcp.formatting import build_rtb_triage_payload

        payload = build_rtb_triage_payload(
            device_id=123,
            issue_summary="test",
            issue_types=["GPU issue"],
            assignee_email="",
        )
        assert "assignee_email" not in payload
