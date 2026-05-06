"""Tests for triage-list CLI command, list_rtb_triage_tickets MCP tool,
and the underlying linear_list_issues() helper (issue #61).
"""

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
import requests
from typer.testing import CliRunner

from dc_support_mcp.cli import app
from dc_support_mcp.oncall import linear_list_issues

runner = CliRunner()

SAMPLE_LINEAR_NODES: list[dict[str, Any]] = [
    {
        "id": "uuid-1",
        "identifier": "SRE-100",
        "title": "GPU Missing - us-south-3a-r07-06",
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"email": "sre@together.ai", "displayName": "SRE Eng"},
        "createdAt": "2026-05-01T10:00:00Z",
        "url": "https://linear.app/together-ai/issue/SRE-100",
    },
    {
        "id": "uuid-2",
        "identifier": "SRE-101",
        "title": "NVSwitch failure - us-south-3a-r08-02",
        "state": {"name": "Todo", "type": "unstarted"},
        "assignee": None,
        "createdAt": "2026-05-02T14:30:00Z",
        "url": "https://linear.app/together-ai/issue/SRE-101",
    },
]


def _graphql_success_response(nodes: list[dict[str, Any]] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {"issues": {"nodes": nodes if nodes is not None else SAMPLE_LINEAR_NODES}}
    }
    return resp


# ── linear_list_issues() unit tests ────────────────────────────────────


@pytest.mark.unit
class TestLinearListIssues:
    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_returns_list_of_issues(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response()

        result = linear_list_issues()
        assert result is not None
        assert len(result) == 2
        assert result[0]["id"] == "SRE-100"
        assert result[0]["status"] == "In Progress"
        assert result[0]["assignee"] == "sre@together.ai"
        assert result[1]["id"] == "SRE-101"
        assert result[1]["assignee"] == ""

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_team_filter_passed_to_graphql(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues(team_key="SRE")
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert call_vars["filter"]["team"] == {"key": {"eq": "SRE"}}

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test", "RTB_LINEAR_TEAM_KEY": "INFRA"})
    def test_env_team_fallback(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues()
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert call_vars["filter"]["team"] == {"key": {"eq": "INFRA"}}

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_assignee_filter(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues(assignee_email="sre@together.ai")
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert call_vars["filter"]["assignee"] == {"email": {"eq": "sre@together.ai"}}

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_closed_status_filter(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues(status="closed")
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert "state" in call_vars["filter"]
        assert call_vars["filter"]["state"]["type"]["in"] == ["completed", "canceled"]

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_all_status_no_state_filter(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues(status="all")
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert "state" not in call_vars["filter"]

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_limit_clamped(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _graphql_success_response([])

        linear_list_issues(limit=100)
        call_vars = mock_post.call_args.kwargs["json"]["variables"]
        assert call_vars["first"] == 50

    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_none(self) -> None:
        result = linear_list_issues()
        assert result is None

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_api_error_returns_none(self, mock_post: MagicMock) -> None:
        mock_post.return_value.status_code = 401
        result = linear_list_issues()
        assert result is None

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_network_error_returns_none(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = requests.ConnectionError("timeout")
        result = linear_list_issues()
        assert result is None

    @patch("dc_support_mcp.oncall.requests.post")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_graphql_errors_returns_none(self, mock_post: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"errors": [{"message": "auth failed"}]}
        mock_post.return_value = resp

        result = linear_list_issues()
        assert result is None


# ── CLI triage-list tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestTriageListCLI:
    SAMPLE_TICKETS: ClassVar[list[dict[str, Any]]] = [
        {
            "id": "SRE-100",
            "title": "GPU Missing - us-south-3a-r07-06",
            "status": "In Progress",
            "status_type": "started",
            "assignee": "sre@together.ai",
            "created": "2026-05-01T10:00:00Z",
            "url": "https://linear.app/together-ai/issue/SRE-100",
        },
    ]

    @patch("dc_support_mcp.cli.os.getenv")
    def test_missing_api_key(self, mock_getenv: MagicMock) -> None:
        mock_getenv.return_value = None
        result = runner.invoke(app, ["triage-list"])
        assert result.exit_code == 1
        assert "LINEAR_API_KEY" in result.output

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_empty_json(self, mock_list: MagicMock) -> None:
        mock_list.return_value = []

        result = runner.invoke(app, ["triage-list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {"tickets": [], "count": 0, "status": "open"}

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_empty_text(self, mock_list: MagicMock) -> None:
        mock_list.return_value = []

        result = runner.invoke(app, ["triage-list"])
        assert result.exit_code == 0
        assert "No triage tickets found" in result.output

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_list_json_output(self, mock_list: MagicMock) -> None:
        mock_list.return_value = self.SAMPLE_TICKETS

        result = runner.invoke(app, ["triage-list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 1
        assert data["tickets"][0]["id"] == "SRE-100"

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_list_text_output(self, mock_list: MagicMock) -> None:
        mock_list.return_value = self.SAMPLE_TICKETS

        result = runner.invoke(app, ["triage-list"])
        assert result.exit_code == 0
        assert "SRE-100" in result.output
        assert "In Progress" in result.output
        assert "sre@together.ai" in result.output

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_passes_filters_through(self, mock_list: MagicMock) -> None:
        mock_list.return_value = []

        runner.invoke(
            app,
            [
                "triage-list",
                "--status", "closed",
                "--assignee", "eng@together.ai",
                "--team", "SRE",
                "--limit", "5",
            ],
        )
        mock_list.assert_called_once_with(
            team_key="SRE",
            assignee_email="eng@together.ai",
            status="closed",
            limit=5,
        )

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_api_failure(self, mock_list: MagicMock) -> None:
        mock_list.return_value = None

        result = runner.invoke(app, ["triage-list"])
        assert result.exit_code == 1
        assert "Failed to query Linear" in result.output

    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_invalid_status(self) -> None:
        result = runner.invoke(app, ["triage-list", "--status", "invalid"])
        assert result.exit_code == 1
        assert "Unknown status" in result.output


# ── MCP list_rtb_triage_tickets tests ──────────────────────────────────


@pytest.mark.unit
class TestListRtbTriageTicketsMCP:
    SAMPLE_TICKETS: ClassVar[list[dict[str, Any]]] = [
        {
            "id": "SRE-200",
            "title": "NCCL failure - us-south-3a-r09-01",
            "status": "Todo",
            "status_type": "unstarted",
            "assignee": "",
            "created": "2026-05-03T08:00:00Z",
            "url": "https://linear.app/together-ai/issue/SRE-200",
        },
    ]

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key(self) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        result = list_rtb_triage_tickets()
        assert "error" in result
        assert "LINEAR_API_KEY" in result["error"]

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_invalid_status(self, mock_list: MagicMock) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        result = list_rtb_triage_tickets(status="invalid")
        assert "error" in result
        assert "Unknown status" in result["error"]

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_success(self, mock_list: MagicMock) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        mock_list.return_value = self.SAMPLE_TICKETS

        result = list_rtb_triage_tickets(status="open", team_key="SRE", limit=10)
        assert result["count"] == 1
        assert result["tickets"][0]["id"] == "SRE-200"
        assert result["status"] == "open"
        mock_list.assert_called_once_with(
            team_key="SRE",
            assignee_email=None,
            status="open",
            limit=10,
        )

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_empty_result(self, mock_list: MagicMock) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        mock_list.return_value = []

        result = list_rtb_triage_tickets()
        assert result["count"] == 0
        assert result["tickets"] == []

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_api_failure(self, mock_list: MagicMock) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        mock_list.return_value = None

        result = list_rtb_triage_tickets()
        assert "error" in result
        assert "Failed to query Linear" in result["error"]

    @patch("dc_support_mcp.oncall.linear_list_issues")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_assignee_filter(self, mock_list: MagicMock) -> None:
        from dc_support_mcp.mcp_server import list_rtb_triage_tickets

        mock_list.return_value = []

        list_rtb_triage_tickets(assignee="eng@together.ai")
        mock_list.assert_called_once_with(
            team_key=None,
            assignee_email="eng@together.ai",
            status="open",
            limit=20,
        )
