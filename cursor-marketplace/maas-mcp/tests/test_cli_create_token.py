"""Tests for the maas-cli create-token command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from typer.testing import CliRunner

from maas_mcp.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _mock_session(
    *,
    login_status: int = 302,
    token_status: int = 200,
    token_json: dict | None = None,
    get_raises: Exception | None = None,
    login_raises: Exception | None = None,
    token_raises: Exception | None = None,
) -> MagicMock:
    """Build a mock requests.Session with configurable responses for the 3-step flow."""
    session = MagicMock()

    if get_raises:
        session.get.side_effect = get_raises
        return session

    csrf_cookie = MagicMock()
    csrf_cookie.get.return_value = "fake-csrf-token"
    session.cookies = csrf_cookie

    get_resp = MagicMock()
    get_resp.status_code = 200
    session.get.return_value = get_resp

    if login_raises:
        session.post.side_effect = login_raises
        return session

    login_resp = MagicMock()
    login_resp.status_code = login_status

    if token_json is None:
        token_json = {
            "consumer_key": "ck111",
            "token_key": "tk222",
            "token_secret": "ts333",
            "name": "agent-token",
        }

    token_resp = MagicMock()
    token_resp.status_code = token_status
    token_resp.json.return_value = token_json
    token_resp.text = json.dumps(token_json)

    if token_raises:
        session.post.side_effect = [login_resp, token_raises]
    else:
        session.post.side_effect = [login_resp, token_resp]

    return session


_BASE_ARGS = [
    "create-token",
    "--url", "http://maas.test:5240/MAAS",
    "--username", "admin",
    "--password", "secret",
]


class TestCreateTokenSuccess:
    def test_text_output(self, runner: CliRunner) -> None:
        session = _mock_session()
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 0
        assert "ck111:tk222:ts333" in result.stdout
        assert "consumer_key: ck111" in result.stdout
        assert "token_key: tk222" in result.stdout
        assert "token_secret: ts333" in result.stdout

    def test_json_output(self, runner: CliRunner) -> None:
        session = _mock_session()
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, [*_BASE_ARGS, "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["api_key"] == "ck111:tk222:ts333"
        assert data["consumer_key"] == "ck111"
        assert data["token_key"] == "tk222"
        assert data["token_secret"] == "ts333"
        assert data["name"] == "agent-token"
        assert data["url"] == "http://maas.test:5240/MAAS"

    def test_custom_token_name(self, runner: CliRunner) -> None:
        session = _mock_session()
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, [*_BASE_ARGS, "--name", "my-token", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["name"] == "my-token"

    def test_csrf_sent_in_login(self, runner: CliRunner) -> None:
        session = _mock_session()
        with patch("requests.Session", return_value=session):
            runner.invoke(app, _BASE_ARGS)

        login_call = session.post.call_args_list[0]
        assert login_call.kwargs["data"]["csrfmiddlewaretoken"] == "fake-csrf-token"
        assert login_call.kwargs["headers"]["X-CSRFToken"] == "fake-csrf-token"


class TestCreateTokenLoginFailure:
    def test_login_401(self, runner: CliRunner) -> None:
        session = _mock_session(login_status=401)
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 1
        assert "login failed" in result.stderr.lower()
        assert "401" in result.stderr

    def test_login_network_error(self, runner: CliRunner) -> None:
        session = _mock_session(login_raises=requests.RequestException("connection refused"))
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 1
        assert "login request failed" in result.stderr.lower()


class TestCreateTokenCreationFailure:
    def test_token_500(self, runner: CliRunner) -> None:
        session = _mock_session(token_status=500)
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 1
        assert "token creation failed" in result.stderr.lower()
        assert "500" in result.stderr

    def test_token_network_error(self, runner: CliRunner) -> None:
        session = _mock_session(token_raises=requests.RequestException("timeout"))
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 1
        assert "token creation request failed" in result.stderr.lower()


class TestCreateTokenUnreachable:
    def test_get_login_page_fails(self, runner: CliRunner) -> None:
        session = _mock_session(get_raises=ConnectionError("DNS resolution failed"))
        with patch("requests.Session", return_value=session):
            result = runner.invoke(app, _BASE_ARGS)

        assert result.exit_code == 1
        assert "could not reach" in result.stderr.lower()
