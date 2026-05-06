"""Unit tests for Weka REST client behavior."""

from __future__ import annotations

import json

import httpx
import pytest

from weka_mcp.weka_client import WekaRestClient

# ── shared helpers ──────────────────────────────────────────────

LOGIN_RESPONSE = {
    "data": [{"access_token": "token-a", "refresh_token": "refresh-a", "expires_in": 300}]
}
REFRESHED_RESPONSE = {
    "data": [{"access_token": "token-b", "refresh_token": "refresh-b", "expires_in": 300}]
}


def _make_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _login_handler(request: httpx.Request) -> httpx.Response | None:
    """Handle login requests in mock transports."""
    if request.url.path.endswith("/api/v2/login/"):
        return httpx.Response(status_code=200, json=LOGIN_RESPONSE)
    return None


def _make_client(handler) -> WekaRestClient:
    return WekaRestClient(
        host="https://weka01:14000",
        username="admin",
        password="secret",
        http_transport=_make_transport(handler),
    )


# ── login + GET ─────────────────────────────────────────────────


def test_login_and_get_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        if request.url.path.endswith("/api/v2/cluster/"):
            assert request.headers.get("Authorization") == "Bearer token-a"
            return httpx.Response(status_code=200, json={"status": "ok"})
        return httpx.Response(status_code=404, json={"error": "not found"})

    with _make_client(handler) as client:
        assert client.get("cluster") == {"status": "ok"}


# ── token refresh ───────────────────────────────────────────────


def test_refresh_token_path_is_used_when_expired() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        resp = _login_handler(request)
        if resp:
            return resp
        if request.url.path.endswith("/api/v2/login/refresh/"):
            return httpx.Response(status_code=200, json=REFRESHED_RESPONSE)
        if request.url.path.endswith("/api/v2/cluster/"):
            assert request.headers.get("Authorization") == "Bearer token-b"
            return httpx.Response(status_code=200, json={"status": "ok"})
        return httpx.Response(status_code=404, json={"error": "not found"})

    with _make_client(handler) as client:
        client._token_expires_at = 0
        assert client.get("cluster") == {"status": "ok"}
        assert any(p.endswith("/api/v2/login/refresh/") for p in calls)


def test_refresh_falls_back_to_login_on_failure() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/api/v2/login/"):
            return httpx.Response(status_code=200, json=LOGIN_RESPONSE)
        if request.url.path.endswith("/api/v2/login/refresh/"):
            return httpx.Response(status_code=401, text="expired")
        if request.url.path.endswith("/api/v2/cluster/"):
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        client._token_expires_at = 0
        client.get("cluster")
        login_calls = [p for p in calls if p.endswith("/api/v2/login/")]
        assert len(login_calls) >= 2  # initial + fallback re-login


# ── error handling ──────────────────────────────────────────────


def test_get_raises_runtime_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        return httpx.Response(status_code=500, text="server error")

    with _make_client(handler) as client:
        with pytest.raises(RuntimeError, match="Weka GET"):
            client.get("cluster")


def test_login_failure_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="bad credentials")

    with pytest.raises(RuntimeError, match="Weka login failed"):
        _make_client(handler)


def test_login_missing_token_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v2/login/"):
            return httpx.Response(status_code=200, json={"data": [{}]})
        return httpx.Response(status_code=404)

    with pytest.raises(RuntimeError, match="Failed to obtain access token"):
        _make_client(handler)


# ── POST / PUT / DELETE ─────────────────────────────────────────


def test_post_sends_json_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        if request.url.path.endswith("/api/v2/fileSystems/"):
            captured["body"] = request.content
            return httpx.Response(status_code=200, json={"uid": "fs-001"})
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        result = client.post("fileSystems", json={"name": "test", "capacity": "1TB"})
        assert result["uid"] == "fs-001"


def test_put_sends_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        if request.method == "PUT":
            return httpx.Response(status_code=200, json={"updated": True})
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        assert client.put("s3", json={"key": "val"}) == {"updated": True}


def test_delete_returns_status_dict_for_empty_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        if request.method == "DELETE":
            return httpx.Response(status_code=204, text="")
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        result = client.delete("fileSystems/fs-001")
        assert result["status"] == "deleted"


def test_delete_returns_json_when_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        if request.method == "DELETE":
            return httpx.Response(status_code=200, json={"removed": "fs-001"})
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        assert client.delete("fileSystems/fs-001") == {"removed": "fs-001"}


# ── context manager ─────────────────────────────────────────────


def test_context_manager() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        resp = _login_handler(request)
        if resp:
            return resp
        return httpx.Response(status_code=200, json={})

    with _make_client(handler) as client:
        assert client._access_token == "token-a"


# ── token parsing edge cases ───────────────────────────────────


def test_login_with_org_includes_org_in_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v2/login/"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(status_code=200, json=LOGIN_RESPONSE)
        return httpx.Response(status_code=404)

    client = WekaRestClient(
        host="https://weka01:14000",
        username="admin",
        password="secret",
        org="my-org",
        http_transport=_make_transport(handler),
    )
    with client:
        assert captured["body"] == {
            "username": "admin",
            "password": "secret",
            "org": "my-org",
        }


def test_flat_token_response_without_data_wrapper() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v2/login/"):
            return httpx.Response(
                status_code=200,
                json={
                    "access_token": "flat-token",
                    "refresh_token": "flat-refresh",
                    "expires_in": 600,
                },
            )
        if request.url.path.endswith("/api/v2/cluster/"):
            return httpx.Response(status_code=200, json={"ok": True})
        return httpx.Response(status_code=404)

    with _make_client(handler) as client:
        assert client._access_token == "flat-token"
        assert client.get("cluster") == {"ok": True}


# Need this import for the POST test type annotation
from typing import Any
