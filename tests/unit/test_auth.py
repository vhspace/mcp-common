"""Tests for HttpAccessTokenAuth middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from mcp_common.auth import HttpAccessTokenAuth


def _make_context(method: str = "tools/call") -> MagicMock:
    ctx = MagicMock()
    ctx.method = method
    return ctx


def _make_request(*, bearer: str | None = None, api_key: str | None = None) -> MagicMock:
    headers: dict[str, str] = {}
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    if api_key is not None:
        headers["x-api-key"] = api_key
    request = MagicMock()
    request.headers = headers
    return request


class TestHttpAccessTokenAuth:
    def test_stores_token(self) -> None:
        mw = HttpAccessTokenAuth("secret-123")
        assert mw._token == "secret-123"

    @pytest.mark.anyio
    async def test_accepts_valid_bearer_token(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock(return_value="ok")
        request = _make_request(bearer="my-token")

        with patch("mcp_common.auth.get_http_request", return_value=request):
            result = await mw.on_request(ctx, call_next)

        assert result == "ok"
        call_next.assert_awaited_once_with(ctx)

    @pytest.mark.anyio
    async def test_accepts_valid_api_key(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock(return_value="ok")
        request = _make_request(api_key="my-token")

        with patch("mcp_common.auth.get_http_request", return_value=request):
            result = await mw.on_request(ctx, call_next)

        assert result == "ok"
        call_next.assert_awaited_once_with(ctx)

    @pytest.mark.anyio
    async def test_rejects_invalid_bearer_token(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock()
        request = _make_request(bearer="wrong-token")

        with (
            patch("mcp_common.auth.get_http_request", return_value=request),
            pytest.raises(ToolError, match="Unauthorized"),
        ):
            await mw.on_request(ctx, call_next)

        call_next.assert_not_awaited()

    @pytest.mark.anyio
    async def test_rejects_invalid_api_key(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock()
        request = _make_request(api_key="wrong-key")

        with (
            patch("mcp_common.auth.get_http_request", return_value=request),
            pytest.raises(ToolError, match="Unauthorized"),
        ):
            await mw.on_request(ctx, call_next)

        call_next.assert_not_awaited()

    @pytest.mark.anyio
    async def test_rejects_missing_credentials(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock()
        request = _make_request()

        with (
            patch("mcp_common.auth.get_http_request", return_value=request),
            pytest.raises(ToolError, match="Unauthorized"),
        ):
            await mw.on_request(ctx, call_next)

    @pytest.mark.anyio
    async def test_allows_initialize_without_auth(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context(method="initialize")
        call_next = AsyncMock(return_value="init-ok")

        result = await mw.on_request(ctx, call_next)

        assert result == "init-ok"
        call_next.assert_awaited_once_with(ctx)

    @pytest.mark.anyio
    async def test_passes_through_when_no_http_request(self) -> None:
        """When running over stdio there is no HTTP request context."""
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock(return_value="stdio-ok")

        with patch("mcp_common.auth.get_http_request", side_effect=RuntimeError):
            result = await mw.on_request(ctx, call_next)

        assert result == "stdio-ok"
        call_next.assert_awaited_once_with(ctx)

    @pytest.mark.anyio
    async def test_bearer_prefix_case_insensitive(self) -> None:
        mw = HttpAccessTokenAuth("my-token")
        ctx = _make_context()
        call_next = AsyncMock(return_value="ok")
        request = MagicMock()
        request.headers = {"authorization": "BEARER my-token", "x-api-key": ""}

        with patch("mcp_common.auth.get_http_request", return_value=request):
            result = await mw.on_request(ctx, call_next)

        assert result == "ok"
