import asyncio

import pytest
from fastmcp.exceptions import ToolError

import awx_mcp.server as server


def test_http_access_token_auth_allows_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(
        server, "get_http_headers", lambda: {"Authorization": "Bearer secret-token"}
    )

    async def call_next(_ctx):
        return {"ok": True}

    out = asyncio.run(middleware.on_call_tool(object(), call_next))
    assert out == {"ok": True}


def test_http_access_token_auth_allows_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(server, "get_http_headers", lambda: {"x-api-key": "secret-token"})

    async def call_next(_ctx):
        return {"ok": True}

    out = asyncio.run(middleware.on_call_tool(object(), call_next))
    assert out == {"ok": True}


def test_http_access_token_auth_allows_case_insensitive_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(
        server,
        "get_http_headers",
        lambda: {"AUTHORIZATION": "bearer secret-token", "X-API-KEY": "secret-token"},
    )

    async def call_next(_ctx):
        return {"ok": True}

    # Test bearer token
    out = asyncio.run(middleware.on_call_tool(object(), call_next))
    assert out == {"ok": True}


def test_http_access_token_auth_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(server, "get_http_headers", lambda: {"Authorization": "Bearer wrong-token"})

    async def call_next(_ctx):
        return {"ok": True}

    try:
        asyncio.run(middleware.on_call_tool(object(), call_next))
        raise AssertionError("expected ToolError")
    except ToolError as e:
        assert "Unauthorized" in str(e)


def test_http_access_token_auth_rejects_malformed_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(server, "get_http_headers", lambda: {"Authorization": "Bearer"})

    async def call_next(_ctx):
        return {"ok": True}

    try:
        asyncio.run(middleware.on_call_tool(object(), call_next))
        raise AssertionError("expected ToolError")
    except ToolError as e:
        assert "Unauthorized" in str(e)


def test_http_access_token_auth_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("secret-token")
    monkeypatch.setattr(server, "get_http_headers", lambda: {})

    async def call_next(_ctx):
        return {"ok": True}

    try:
        asyncio.run(middleware.on_call_tool(object(), call_next))
        raise AssertionError("expected ToolError")
    except ToolError as e:
        assert "Unauthorized" in str(e)


def test_http_access_token_auth_rejects_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = server.HttpAccessTokenAuth("")
    monkeypatch.setattr(server, "get_http_headers", lambda: {"Authorization": "Bearer "})

    async def call_next(_ctx):
        return {"ok": True}

    try:
        asyncio.run(middleware.on_call_tool(object(), call_next))
        raise AssertionError("expected ToolError")
    except ToolError as e:
        assert "Unauthorized" in str(e)
