"""Enforced read-only (``MCP_ENFORCE_READONLY``) tests for ufm-cli.

Every UFM tool is ``@dual_mode_tool(mcp_only=True)``, so ``build_cli_from_mcp``
synthesizes no commands and the synthesized-command read-only gate never applies
to ufm-cli. Each hand-written WRITE ``@app.command()`` therefore carries an
explicit ``@enforce_read_only_cli(read_only=False)`` gate (and ``pkey-diff
--apply`` an inline ``refuse_if_read_only_blocked`` guard) so that under
``MCP_ENFORCE_READONLY`` no fabric mutation is issued from the CLI — matching the
write-tool classification (``read_only=False`` / ``tags={"write"}``) on the MCP
surface (mcp-common #148).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from mcp_common.dual_mode import READONLY_REFUSAL_MESSAGE
from mcp_common.testing.dual_mode import make_cli_runner

from ufm_mcp.cli import app

runner = make_cli_runner()


@pytest.fixture(autouse=True)
def _stub_init():
    """Skip the env-loading + site-manager init ufm-cli does at startup."""
    with patch("ufm_mcp.cli._ensure_init"):
        yield


# (argv, write tool the command dispatches to) for every hand-written ufm-cli
# command that maps to a write tool on the MCP surface. The write tools are
# imported inside each command body, so patching ``ufm_mcp.server.<tool>`` lets
# us assert the gate fires *before* the body (the tool is never invoked).
WRITE_COMMANDS = [
    (["pkey-add-guids", "0x1", "0xaaa", "-s", "ori"], "ufm_add_guids_to_pkey"),
    (["pkey-remove-guids", "0x1", "0xaaa", "-s", "ori"], "ufm_remove_guids_from_pkey"),
    (["pkey-remove-hosts", "0x1", "node01", "-s", "ori"], "ufm_remove_hosts_from_pkey"),
    (["pkey-add-hosts", "0x1", "node01", "-s", "ori"], "ufm_add_hosts_to_pkey"),
    (["upload-ibdiagnet", "/tmp/ibdiagnet.tar.gz", "--site", "ori"], "ufm_upload_ibdiagnet"),
]
WRITE_IDS = [argv[0] for argv, _ in WRITE_COMMANDS]
WRITE_ARGS = [argv for argv, _ in WRITE_COMMANDS]


@pytest.mark.parametrize(("argv", "tool_name"), WRITE_COMMANDS, ids=WRITE_IDS)
@pytest.mark.parametrize("mode", ["1", "strict"])
def test_write_command_refused_and_not_executed(
    argv: list[str], tool_name: str, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each write command is refused and never dispatches its tool under enforce mode."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", mode)
    with patch(f"ufm_mcp.server.{tool_name}") as mock_tool:
        result = runner.invoke(app, argv)

    assert result.exit_code != 0, result.output
    assert result.stderr.strip() == READONLY_REFUSAL_MESSAGE
    mock_tool.assert_not_called()


@pytest.mark.parametrize("argv", WRITE_ARGS, ids=WRITE_IDS)
def test_write_command_help_works_under_enforce(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--help`` short-circuits before the gate, so it works even under enforce mode."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    result = runner.invoke(app, [argv[0], "--help"])

    assert result.exit_code == 0, result.stderr
    assert argv[0] in result.output


@pytest.mark.parametrize(("argv", "tool_name"), WRITE_COMMANDS, ids=WRITE_IDS)
def test_write_command_runs_when_disabled(
    argv: list[str], tool_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the toggle unset the gate is a transparent pass-through — the tool runs."""
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    with patch(f"ufm_mcp.server.{tool_name}", return_value={"ok": True}) as mock_tool:
        result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.stderr
    mock_tool.assert_called_once()


def test_read_command_allowed_under_enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read command (``pkeys``) is never blocked, even under enforce mode."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    fake = {"ok": True, "pkeys": ["0x1", "0x7fff"]}
    with patch("ufm_mcp.server.ufm_list_pkeys", return_value=fake) as mock_tool:
        result = runner.invoke(app, ["pkeys", "-s", "ori", "-j"])

    assert result.exit_code == 0, result.stderr
    mock_tool.assert_called_once()
    assert "0x1" in result.output


def test_pkey_diff_read_path_allowed_under_enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pkey-diff`` without ``--apply`` is read-only and runs under enforce mode."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    fake = {
        "ok": True,
        "current_hosts_count": 1,
        "expected_hosts_count": 1,
        "to_add": [],
        "to_remove": [],
        "unchanged": ["node01"],
    }
    with patch("ufm_mcp.server.ufm_pkey_diff", return_value=fake) as mock_diff:
        result = runner.invoke(app, ["pkey-diff", "0x1", "--expected", "node01", "-s", "ori"])

    assert result.exit_code == 0, result.stderr
    mock_diff.assert_called_once()


def test_pkey_diff_apply_refused_and_no_write_under_enforce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pkey-diff --apply`` dispatches a write tool, so it is refused under enforce mode."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    with (
        patch("ufm_mcp.server.ufm_pkey_diff") as mock_diff,
        patch("ufm_mcp.server.ufm_add_hosts_to_pkey") as mock_write,
    ):
        result = runner.invoke(
            app,
            ["pkey-diff", "0x1", "--expected", "node01", "-s", "ori", "--apply", "--yes"],
        )

    assert result.exit_code != 0, result.output
    assert result.stderr.strip() == READONLY_REFUSAL_MESSAGE
    # The early --apply guard fires before both the read diff and the write.
    mock_write.assert_not_called()
    mock_diff.assert_not_called()


def test_pkey_diff_apply_runs_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the toggle unset, ``pkey-diff --apply`` still performs its write."""
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    diff = {
        "ok": True,
        "current_hosts_count": 0,
        "expected_hosts_count": 1,
        "to_add": ["node01"],
        "to_remove": [],
        "unchanged": [],
    }
    with (
        patch("ufm_mcp.server.ufm_pkey_diff", return_value=diff),
        patch(
            "ufm_mcp.server.ufm_add_hosts_to_pkey",
            return_value={"ok": True, "hosts_added": 1},
        ) as mock_write,
    ):
        result = runner.invoke(
            app,
            ["pkey-diff", "0x1", "--expected", "node01", "-s", "ori", "--apply", "--yes"],
        )

    assert result.exit_code == 0, result.stderr
    mock_write.assert_called_once()
