"""Tests for eval remediation agent dispatch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcp_common.testing.eval.analyzer import EvalFailure
from mcp_common.testing.eval.remediate import (
    _build_remediation_prompt,
    _extract_issue_number,
    _extract_pr_url,
    remediate_batch,
    remediate_failure,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_failure(
    *,
    server: str = "netbox-mcp",
    scenario: str = "list all devices in the datacenter",
    tool_calls: list[str] | None = None,
    error: str = "Tool selection: 0.00 (called [], expected ['list_devices'])",
    score: str = "I",
    trace_excerpt: str = "I don't know how to do that.",
) -> EvalFailure:
    return EvalFailure(
        server=server,
        scenario=scenario,
        tool_calls=tool_calls or [],
        error=error,
        score=score,
        trace_excerpt=trace_excerpt,
    )


# ---------------------------------------------------------------------------
# _build_remediation_prompt
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestBuildRemediationPrompt:
    def test_contains_issue_url(self) -> None:
        f = _make_failure()
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/42")
        assert "https://github.com/vhspace/netbox-mcp/issues/42" in prompt

    def test_contains_scenario(self) -> None:
        f = _make_failure(scenario="find the device named web01")
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "find the device named web01" in prompt

    def test_contains_error(self) -> None:
        f = _make_failure(error="Tool selection: wrong tool called")
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "Tool selection: wrong tool called" in prompt

    def test_contains_trace_excerpt(self) -> None:
        f = _make_failure(trace_excerpt="Agent said: I cannot help")
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "Agent said: I cannot help" in prompt

    def test_contains_tool_calls(self) -> None:
        f = _make_failure(tool_calls=["get_device", "search_ip"])
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "get_device" in prompt
        assert "search_ip" in prompt

    def test_no_tool_calls(self) -> None:
        f = _make_failure(tool_calls=[])
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "None" in prompt

    def test_extracts_expected_tools(self) -> None:
        f = _make_failure(error="Tool selection: 0.00 (called [], expected ['list_devices'])")
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/1")
        assert "'list_devices'" in prompt

    def test_branch_name_includes_issue_number(self) -> None:
        f = _make_failure()
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/99")
        assert "fix/eval-99" in prompt

    def test_branch_name_unknown_number(self) -> None:
        f = _make_failure()
        prompt = _build_remediation_prompt(f, "https://example.com/no-number")
        assert "fix/eval-unknown" in prompt

    def test_instructions_section(self) -> None:
        f = _make_failure()
        prompt = _build_remediation_prompt(f, "https://github.com/vhspace/netbox-mcp/issues/7")
        assert "## Instructions" in prompt
        assert "Create a branch" in prompt
        assert "Open a PR" in prompt


# ---------------------------------------------------------------------------
# _extract_issue_number / _extract_pr_url
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestHelpers:
    def test_extract_issue_number(self) -> None:
        assert _extract_issue_number("https://github.com/vhspace/netbox-mcp/issues/42") == "42"

    def test_extract_issue_number_no_match(self) -> None:
        assert _extract_issue_number("https://example.com") == "unknown"

    def test_extract_pr_url(self) -> None:
        output = "some text\nhttps://github.com/vhspace/netbox-mcp/pull/10\nmore text"
        assert _extract_pr_url(output) == "https://github.com/vhspace/netbox-mcp/pull/10"

    def test_extract_pr_url_none(self) -> None:
        assert _extract_pr_url("no PR here") is None


# ---------------------------------------------------------------------------
# remediate_failure — dry-run mode
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestRemediateFailureDryRun:
    def test_dry_run_prints_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/42",
            dry_run=True,
        )
        assert result is None
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "netbox-mcp" in captured.out

    def test_dry_run_claude_shows_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure(server="maas-mcp")
        remediate_failure(
            f,
            "https://github.com/vhspace/maas-mcp/issues/5",
            agent_backend="claude",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert "claude" in captured.out
        assert "maas-mcp" in captured.out

    def test_dry_run_cursor_shows_placeholder(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure()
        remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            agent_backend="cursor",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "cursor" in captured.out.lower()

    def test_unsupported_backend_returns_none(self) -> None:
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            agent_backend="gpt-pilot",
            dry_run=True,
        )
        assert result is None


# ---------------------------------------------------------------------------
# remediate_failure — mocked subprocess
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestRemediateFailureMocked:
    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("mcp_common.testing.eval.remediate.subprocess.run")
    def test_claude_success(self, mock_run: MagicMock, _which: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Created PR: https://github.com/vhspace/netbox-mcp/pull/55\nDone.",
            stderr="",
        )
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/42",
            workspace_root="/tmp/ws",
            dry_run=False,
        )
        assert result == "https://github.com/vhspace/netbox-mcp/pull/55"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert str(mock_run.call_args[1]["cwd"]) == "/tmp/ws/netbox-mcp"

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("mcp_common.testing.eval.remediate.subprocess.run")
    def test_claude_no_pr_url(self, mock_run: MagicMock, _which: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="Fixed the issue.", stderr="")
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            workspace_root="/tmp/ws",
            dry_run=False,
        )
        assert result is None

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("mcp_common.testing.eval.remediate.subprocess.run")
    def test_claude_nonzero_exit(self, mock_run: MagicMock, _which: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            workspace_root="/tmp/ws",
            dry_run=False,
        )
        assert result is None

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch(
        "mcp_common.testing.eval.remediate.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
    )
    def test_claude_timeout(self, _run: MagicMock, _which: MagicMock) -> None:
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            workspace_root="/tmp/ws",
            dry_run=False,
        )
        assert result is None

    @patch("shutil.which", return_value=None)
    def test_claude_not_installed(
        self, _which: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            workspace_root="/tmp/ws",
            dry_run=False,
        )
        assert result is None
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()


# ---------------------------------------------------------------------------
# remediate_batch
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestRemediateBatch:
    def test_batch_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        failures = [
            _make_failure(server="netbox-mcp", scenario="find device"),
            _make_failure(server="maas-mcp", scenario="list machines"),
        ]
        issue_urls = {
            "netbox-mcp|find device": "https://github.com/vhspace/netbox-mcp/issues/10",
            "maas-mcp|list machines": "https://github.com/vhspace/maas-mcp/issues/20",
        }
        pr_urls = remediate_batch(failures, issue_urls, dry_run=True)
        assert pr_urls == []
        captured = capsys.readouterr()
        assert captured.out.count("[DRY RUN]") == 2

    def test_batch_skips_missing_urls(self, capsys: pytest.CaptureFixture[str]) -> None:
        failures = [_make_failure(server="netbox-mcp", scenario="find device")]
        pr_urls = remediate_batch(failures, {}, dry_run=True)
        assert pr_urls == []

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("mcp_common.testing.eval.remediate.subprocess.run")
    def test_batch_collects_pr_urls(self, mock_run: MagicMock, _which: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/vhspace/netbox-mcp/pull/100",
            stderr="",
        )
        failures = [_make_failure(server="netbox-mcp", scenario="find device")]
        issue_urls = {"netbox-mcp|find device": "https://github.com/vhspace/netbox-mcp/issues/50"}
        pr_urls = remediate_batch(failures, issue_urls, workspace_root="/tmp/ws", dry_run=False)
        assert len(pr_urls) == 1
        assert "pull/100" in pr_urls[0]

    def test_batch_empty(self) -> None:
        assert remediate_batch([], {}, dry_run=True) == []


# ---------------------------------------------------------------------------
# CLI integration — --auto-fix flag
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestReportCLIAutoFix:
    def test_auto_fix_dry_run(self, tmp_path: Path) -> None:
        """--auto-fix with --dry-run shows what would run without executing."""
        from typer.testing import CliRunner

        from mcp_common.testing.eval.report import app

        runner = CliRunner()
        result = runner.invoke(app, ["--log-dir", str(tmp_path), "--auto-fix"])
        # Empty directory → no failures → exits cleanly
        assert result.exit_code == 0
        assert "no failures found" in result.stdout.lower()

    def test_auto_fix_with_failures(self, tmp_path: Path) -> None:
        """--auto-fix dispatches remediation after filing issues (mocked)."""
        from typer.testing import CliRunner

        from mcp_common.testing.eval.report import app

        failures = [_make_failure()]

        with (
            patch("mcp_common.testing.eval.report.analyze_eval_dir", return_value=failures),
            patch("mcp_common.testing.eval.report.deduplicate", return_value=failures),
            patch("mcp_common.testing.eval.report.file_issues", return_value=[]),
            patch("mcp_common.testing.eval.report.remediate_batch", return_value=[]) as mock_batch,
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["--log-dir", str(tmp_path), "--auto-fix"])

        assert result.exit_code == 0
        assert "Remediation" in result.stdout
        mock_batch.assert_called_once()
        call_kwargs = mock_batch.call_args
        assert call_kwargs[1]["dry_run"] is True


# ---------------------------------------------------------------------------
# Agent backend selection
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestAgentBackendSelection:
    def test_claude_backend(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure()
        remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            agent_backend="claude",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert "claude" in captured.out

    def test_cursor_backend(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure()
        remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            agent_backend="cursor",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert "cursor" in captured.out.lower()

    def test_invalid_backend(self) -> None:
        f = _make_failure()
        result = remediate_failure(
            f,
            "https://github.com/vhspace/netbox-mcp/issues/1",
            agent_backend="invalid",
            dry_run=True,
        )
        assert result is None
