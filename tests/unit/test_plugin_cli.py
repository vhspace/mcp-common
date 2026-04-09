"""Tests for plugin_cli helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_common.plugin_cli import _referenced_env_vars, app


def test_referenced_env_vars_extracts_curly_refs_only() -> None:
    refs = _referenced_env_vars(
        {
            "A": "${TOKEN_A}",
            "B": "${TOKEN_B}",
            "C": "literal",
            "D": " ${TOKEN_A} ",
        }
    )
    assert refs == ["TOKEN_A", "TOKEN_B"]


def _write_minimal_plugin_project(path: Path) -> None:
    (path / "mcp-plugin.toml").write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n'
    )
    (path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "example-mcp"\n'
        'version = "1.2.3"\n'
        'description = "Example MCP server"\n'
        'requires-python = ">=3.12"\n'
    )


def test_doctor_op_checks_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_minimal_plugin_project(tmp_path)
    monkeypatch.setattr(
        "mcp_common.plugin_cli.op_cli_version_line",
        lambda **_: (True, "2.30.0"),
    )
    monkeypatch.setattr(
        "mcp_common.plugin_cli.op_authenticated",
        lambda **_: (True, ["auth: ok"]),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert result.exit_code == 0
    assert "All checks passed." in result.stdout


def test_doctor_op_auth_failure_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_minimal_plugin_project(tmp_path)
    monkeypatch.setattr(
        "mcp_common.plugin_cli.op_cli_version_line",
        lambda **_: (True, "2.30.0"),
    )
    monkeypatch.setattr(
        "mcp_common.plugin_cli.op_authenticated",
        lambda **_: (False, ["auth: FAIL — not authenticated"]),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert result.exit_code == 1
    assert "1Password CLI/session not ready." in (result.stdout + result.stderr)


def test_doctor_op_cli_missing_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_minimal_plugin_project(tmp_path)
    monkeypatch.setattr(
        "mcp_common.plugin_cli.op_cli_version_line",
        lambda **_: (False, "missing/unavailable"),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert result.exit_code == 1
