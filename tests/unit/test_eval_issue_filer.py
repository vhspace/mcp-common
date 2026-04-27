"""Tests for eval issue filer: deduplicate, file_issues, formatting helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcp_common.testing.eval.analyzer import EvalFailure
from mcp_common.testing.eval.issue_filer import (
    _fingerprint,
    _format_issue_body,
    _format_issue_title,
    deduplicate,
    file_issues,
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
# Fingerprint tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestFingerprint:
    def test_deterministic(self) -> None:
        f1 = _make_failure(scenario="same input")
        f2 = _make_failure(scenario="same input")
        assert _fingerprint(f1) == _fingerprint(f2)

    def test_different_for_different_scenarios(self) -> None:
        f1 = _make_failure(scenario="scenario A")
        f2 = _make_failure(scenario="scenario B")
        assert _fingerprint(f1) != _fingerprint(f2)

    def test_different_for_different_servers(self) -> None:
        f1 = _make_failure(server="server-a")
        f2 = _make_failure(server="server-b")
        assert _fingerprint(f1) != _fingerprint(f2)

    def test_length(self) -> None:
        fp = _fingerprint(_make_failure())
        assert len(fp) == 16

    def test_includes_first_tool(self) -> None:
        f1 = _make_failure(tool_calls=["tool_a", "tool_b"])
        f2 = _make_failure(tool_calls=["tool_c", "tool_b"])
        assert _fingerprint(f1) != _fingerprint(f2)

    def test_no_tools_still_works(self) -> None:
        f = _make_failure(tool_calls=[])
        fp = _fingerprint(f)
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDeduplicate:
    @patch("mcp_common.testing.eval.issue_filer._get_existing_issue_titles", return_value=[])
    def test_removes_exact_duplicates(self, _mock: MagicMock) -> None:
        f1 = _make_failure(scenario="same scenario")
        f2 = _make_failure(scenario="same scenario")
        result = deduplicate([f1, f2])
        assert len(result) == 1

    @patch("mcp_common.testing.eval.issue_filer._get_existing_issue_titles", return_value=[])
    def test_keeps_unique_failures(self, _mock: MagicMock) -> None:
        f1 = _make_failure(scenario="scenario A")
        f2 = _make_failure(scenario="scenario B")
        result = deduplicate([f1, f2])
        assert len(result) == 2

    @patch("mcp_common.testing.eval.issue_filer._get_existing_issue_titles", return_value=[])
    def test_empty_input(self, _mock: MagicMock) -> None:
        result = deduplicate([])
        assert result == []

    def test_skips_existing_issues(self) -> None:
        f = _make_failure(scenario="known failure")
        fp = _fingerprint(f)
        existing_title = f"eval: known failure [{f.score}] ({fp})"

        with patch(
            "mcp_common.testing.eval.issue_filer._get_existing_issue_titles",
            return_value=[existing_title],
        ):
            result = deduplicate([f])
        assert len(result) == 0

    def test_keeps_new_failures_with_existing(self) -> None:
        f_old = _make_failure(scenario="known failure")
        f_new = _make_failure(scenario="new failure")
        fp_old = _fingerprint(f_old)
        existing_title = f"eval: known failure [{f_old.score}] ({fp_old})"

        with patch(
            "mcp_common.testing.eval.issue_filer._get_existing_issue_titles",
            return_value=[existing_title],
        ):
            result = deduplicate([f_old, f_new])
        assert len(result) == 1
        assert result[0].scenario == "new failure"

    @patch("mcp_common.testing.eval.issue_filer._get_existing_issue_titles", return_value=[])
    def test_with_explicit_repo(self, _mock: MagicMock) -> None:
        f = _make_failure()
        result = deduplicate([f], repo="vhspace/netbox-mcp")
        assert len(result) == 1
        _mock.assert_called_once_with("vhspace/netbox-mcp")


# ---------------------------------------------------------------------------
# Issue title/body formatting tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestFormatIssueTitle:
    def test_short_scenario(self) -> None:
        f = _make_failure(scenario="list devices", score="I")
        title = _format_issue_title(f)
        assert title.startswith("eval: list devices")
        assert "[I]" in title

    def test_long_scenario_truncated(self) -> None:
        long_scenario = "x" * 100
        f = _make_failure(scenario=long_scenario, score="P")
        title = _format_issue_title(f)
        assert len(title.split("[")[0].strip()) <= len("eval: ") + 61  # 60 chars + ellipsis

    def test_includes_fingerprint(self) -> None:
        f = _make_failure()
        title = _format_issue_title(f)
        fp = _fingerprint(f)
        assert fp in title


@pytest.mark.eval
class TestFormatIssueBody:
    def test_contains_sections(self) -> None:
        f = _make_failure(
            server="netbox-mcp",
            error="Tool selection: 0.00",
            trace_excerpt="Agent said nothing useful",
            tool_calls=["get_device"],
        )
        body = _format_issue_body(f)
        assert "## Summary" in body
        assert "## Scorer Explanation" in body
        assert "## Eval Trace" in body
        assert "## Tools Called" in body
        assert "## Expected Tools" in body
        assert "## Suggested Fix Category" in body
        assert "`netbox-mcp`" in body
        assert "get_device" in body

    def test_no_tools_shows_none(self) -> None:
        f = _make_failure(tool_calls=[])
        body = _format_issue_body(f)
        assert "(none)" in body


# ---------------------------------------------------------------------------
# file_issues tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestFileIssues:
    def test_dry_run_no_subprocess(self, capsys: pytest.CaptureFixture[str]) -> None:
        f = _make_failure()
        urls = file_issues([f], dry_run=True)
        assert urls == []
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "netbox-mcp" in captured.out

    def test_dry_run_multiple(self, capsys: pytest.CaptureFixture[str]) -> None:
        failures = [
            _make_failure(scenario="fail A"),
            _make_failure(scenario="fail B", server="maas-mcp"),
        ]
        urls = file_issues(failures, dry_run=True)
        assert urls == []
        captured = capsys.readouterr()
        assert captured.out.count("[DRY RUN]") == 2

    def test_create_issues_calls_gh(self) -> None:
        f = _make_failure()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/vhspace/netbox-mcp/issues/42\n"

        with patch("mcp_common.testing.eval.issue_filer.subprocess.run", return_value=mock_result) as mock_run:
            urls = file_issues([f], dry_run=False)

        assert len(urls) == 1
        assert "issues/42" in urls[0]

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "gh"
        assert "issue" in cmd
        assert "create" in cmd
        assert "--repo" in cmd
        assert "vhspace/netbox-mcp" in cmd

    def test_create_issues_handles_failure(self) -> None:
        f = _make_failure()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "repository not found"

        with patch("mcp_common.testing.eval.issue_filer.subprocess.run", return_value=mock_result):
            urls = file_issues([f], dry_run=False)

        assert urls == []

    def test_custom_repo_prefix(self) -> None:
        f = _make_failure(server="my-server")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/myorg/my-server/issues/1\n"

        with patch("mcp_common.testing.eval.issue_filer.subprocess.run", return_value=mock_result) as mock_run:
            file_issues([f], dry_run=False, repo_prefix="myorg")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "myorg/my-server" in cmd

    def test_empty_failures(self) -> None:
        urls = file_issues([], dry_run=True)
        assert urls == []
        urls = file_issues([], dry_run=False)
        assert urls == []

    def test_subprocess_timeout(self) -> None:
        """gh CLI timeout is handled gracefully."""
        import subprocess

        f = _make_failure()
        with patch(
            "mcp_common.testing.eval.issue_filer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30),
        ):
            urls = file_issues([f], dry_run=False)
        assert urls == []


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestReportCLI:
    def test_report_cli_dry_run(self, tmp_path: Path) -> None:
        """CLI runs in dry-run mode on empty directory."""
        from typer.testing import CliRunner

        from mcp_common.testing.eval.report import app

        runner = CliRunner()
        result = runner.invoke(app, ["--log-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no failures found" in result.stdout.lower()
