"""Tests for the linear_attach_url MCP tool and CLI command (issue #45).

The underlying ``oncall.linear_attach_url`` helper (GraphQL mutation/variables
and error handling) is covered in ``test_oncall.py``. These tests focus on the
MCP tool wrapper and the ``dc-support-cli linear-attach-url`` subcommand.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dc_support_mcp.cli import app

runner = CliRunner()


# ── CLI linear-attach-url tests ─────────────────────────────────────────


@pytest.mark.unit
class TestLinearAttachUrlCLI:
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key(self) -> None:
        result = runner.invoke(
            app,
            ["linear-attach-url", "SRE-1", "--url", "https://example.com", "--title", "Example"],
        )
        assert result.exit_code == 1
        assert "LINEAR_API_KEY" in result.output

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_success_json(self, mock_attach: MagicMock) -> None:
        mock_attach.return_value = {
            "ok": True,
            "attachment": {"id": "att-1", "url": "https://example.com", "title": "Example"},
        }

        result = runner.invoke(
            app,
            [
                "linear-attach-url",
                "SRE-1",
                "--url",
                "https://example.com",
                "--title",
                "Example",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["attachment"]["id"] == "att-1"

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_passes_args_through(self, mock_attach: MagicMock) -> None:
        mock_attach.return_value = {"ok": True, "attachment": {}}

        runner.invoke(
            app,
            [
                "linear-attach-url",
                "SRE-1574",
                "--url",
                "https://github.com/org/repo/pull/7",
                "--title",
                "PR #7",
                "--subtitle",
                "Fix the bug",
            ],
        )
        mock_attach.assert_called_once_with(
            issue_id="SRE-1574",
            url="https://github.com/org/repo/pull/7",
            title="PR #7",
            subtitle="Fix the bug",
        )

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_empty_subtitle_becomes_none(self, mock_attach: MagicMock) -> None:
        mock_attach.return_value = {"ok": True, "attachment": {}}

        runner.invoke(
            app,
            ["linear-attach-url", "SRE-1", "--url", "https://example.com", "--title", "Example"],
        )
        mock_attach.assert_called_once_with(
            issue_id="SRE-1",
            url="https://example.com",
            title="Example",
            subtitle=None,
        )

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_error_exits_1(self, mock_attach: MagicMock) -> None:
        mock_attach.return_value = {"error": "Linear returned HTTP 401: unauthorized"}

        result = runner.invoke(
            app,
            ["linear-attach-url", "SRE-1", "--url", "https://example.com", "--title", "Example"],
        )
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "401" in combined


# ── MCP linear_attach_url tool tests ────────────────────────────────────


@pytest.mark.unit
class TestLinearAttachUrlMCP:
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key(self) -> None:
        from dc_support_mcp.mcp_server import linear_attach_url

        result = linear_attach_url("SRE-1", "https://example.com", "Example")
        assert "error" in result
        assert "LINEAR_API_KEY" in result["error"]

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_success(self, mock_attach: MagicMock) -> None:
        from dc_support_mcp.mcp_server import linear_attach_url

        mock_attach.return_value = {"ok": True, "attachment": {"id": "att-1"}}

        result = linear_attach_url("SRE-1", "https://example.com", "Example", subtitle="Sub")
        assert result["ok"] is True
        mock_attach.assert_called_once_with(
            issue_id="SRE-1",
            url="https://example.com",
            title="Example",
            subtitle="Sub",
        )

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_empty_subtitle_becomes_none(self, mock_attach: MagicMock) -> None:
        from dc_support_mcp.mcp_server import linear_attach_url

        mock_attach.return_value = {"ok": True, "attachment": {}}

        linear_attach_url("SRE-1", "https://example.com", "Example")
        mock_attach.assert_called_once_with(
            issue_id="SRE-1",
            url="https://example.com",
            title="Example",
            subtitle=None,
        )

    @patch("dc_support_mcp.oncall.linear_attach_url")
    @patch.dict("os.environ", {"LINEAR_API_KEY": "lin_test"})
    def test_error_propagated(self, mock_attach: MagicMock) -> None:
        from dc_support_mcp.mcp_server import linear_attach_url

        mock_attach.return_value = {"error": "Linear GraphQL errors: [...]"}

        result = linear_attach_url("SRE-bad", "https://example.com", "Example")
        assert "error" in result
