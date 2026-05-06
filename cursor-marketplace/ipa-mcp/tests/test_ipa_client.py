"""Tests for IPAClient class."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from ipa_mcp.ipa_client import IPAClient


def _login_response(cookie: str = "test-session-cookie") -> dict:
    return {"headers": {"Set-Cookie": f"ipa_session={cookie}"}}


def _json_response(result: dict) -> dict:
    return {"json": {"result": result}}


def test_login_posts_to_session_endpoint(httpx_mock: HTTPXMock) -> None:
    """Mock the login POST, verify it sends correct user/password form data."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password",
        method="POST",
        **_login_response(),
    )
    IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    requests = httpx_mock.get_requests()
    assert len(requests) >= 1
    login_req = next(r for r in requests if "login_password" in str(r.url))
    body = login_req.content.decode()
    assert "user=admin" in body
    assert "password=secret" in body


def test_login_sets_session_cookie(httpx_mock: HTTPXMock) -> None:
    """Verify the ipa_session cookie is extracted from response."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password",
        method="POST",
        **_login_response("my-ipa-session-123"),
    )
    client = IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    assert client._session_cookie == "my-ipa-session-123"


def test_call_sends_jsonrpc(httpx_mock: HTTPXMock) -> None:
    """Verify _call sends correct JSON-RPC format with method, params, version."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password", **_login_response()
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/json",
        method="POST",
        **_json_response({"cn": ["test"]}),
    )
    client = IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    client.group_find("test")
    requests = httpx_mock.get_requests()
    json_req = next(r for r in requests if "/ipa/json" in str(r.url))
    payload = json.loads(json_req.content)
    assert payload["method"] == "group_find"
    assert payload["params"][0] == ["test"]
    assert "version" in payload["params"][1]


def test_call_includes_session_cookie(httpx_mock: HTTPXMock) -> None:
    """Verify the session cookie is sent in requests."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password", **_login_response("sess-xyz")
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/json", method="POST", **_json_response({})
    )
    client = IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    client.group_find("")
    requests = httpx_mock.get_requests()
    json_req = next(r for r in requests if "/ipa/json" in str(r.url))
    assert "ipa_session=sess-xyz" in (json_req.headers.get("Cookie") or "")


def test_call_retries_on_401(httpx_mock: HTTPXMock) -> None:
    """Verify re-login on 401."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password", **_login_response("first")
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password", **_login_response("second")
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/json",
        method="POST",
        status_code=401,
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/json",
        method="POST",
        **_json_response({"cn": ["ok"]}),
    )
    client = IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    result = client.group_find("")
    assert result == {"cn": ["ok"]}
    json_requests = [r for r in httpx_mock.get_requests() if "/ipa/json" in str(r.url)]
    assert len(json_requests) == 2


def test_user_show_sends_all_true(httpx_mock: HTTPXMock) -> None:
    """user_show should call IPA user_show with all=True."""
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/session/login_password", **_login_response()
    )
    httpx_mock.add_response(
        url="https://ipa.example.com/ipa/json",
        method="POST",
        **_json_response({"uid": ["testuser"], "memberof_group": ["admins"]}),
    )
    client = IPAClient(
        host="https://ipa.example.com",
        username="admin",
        password="secret",
    )
    client.user_show("testuser")
    requests = httpx_mock.get_requests()
    json_req = next(r for r in requests if "/ipa/json" in str(r.url))
    payload = json.loads(json_req.content)
    assert payload["method"] == "user_show"
    assert payload["params"][0] == ["testuser"]
    assert payload["params"][1]["all"] is True
