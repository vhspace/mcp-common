"""Tests for shared 1Password CLI readiness helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_common.onepassword_cli import op_authenticated, op_cli_version_line


def test_op_cli_version_line_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        assert cmd == ["op", "--version"]
        m = MagicMock()
        m.returncode = 0
        m.stdout = "2.30.0\n"
        m.stderr = ""
        return m

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", fake_run)
    ok, line = op_cli_version_line()
    assert ok is True
    assert line == "2.30.0"


def test_op_cli_version_line_missing_op(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_a: object, **_k: object) -> MagicMock:
        raise FileNotFoundError()

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", fake_run)
    ok, line = op_cli_version_line()
    assert ok is False
    assert line == "missing/unavailable"


def test_op_authenticated_service_account_skips_whoami(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "tok")

    def should_not_run(*_a: object, **_k: object) -> MagicMock:
        raise AssertionError("subprocess.run must not be called when token is set")

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", should_not_run)
    ok, lines = op_authenticated()
    assert ok is True
    assert len(lines) == 1
    assert "OP_SERVICE_ACCOUNT_TOKEN" in lines[0]


def test_op_authenticated_whoami_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        assert cmd == ["op", "whoami"]
        m = MagicMock()
        m.returncode = 0
        m.stdout = "user@example.com\n"
        m.stderr = ""
        return m

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", fake_run)
    ok, lines = op_authenticated()
    assert ok is True
    assert any("whoami" in line for line in lines)


def test_op_authenticated_whoami_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        assert cmd == ["op", "whoami"]
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = "not signed in\n"
        return m

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", fake_run)
    ok, lines = op_authenticated()
    assert ok is False
    assert any("FAIL" in line for line in lines)
    assert any("not signed in" in line for line in lines)


def test_op_authenticated_whoami_file_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        assert cmd == ["op", "whoami"]
        raise FileNotFoundError()

    monkeypatch.setattr("mcp_common.onepassword_cli.subprocess.run", fake_run)
    ok, lines = op_authenticated()
    assert ok is False
    assert any("FAIL" in line for line in lines)
