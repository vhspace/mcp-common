"""Tests-of-tests for the HTTP transport fixtures factory (#94)."""

from __future__ import annotations

from functools import lru_cache

import httpx
import pytest
from fastmcp import FastMCP

from mcp_common.config import MCPSettings
from mcp_common.http import add_health_route, create_http_app
from mcp_common.testing import (
    bearer_headers,
    make_http_transport_fixtures,
    reset_lru_caches,
)

# ---------------------------------------------------------------------------
# A representative fake MCP server module, mirroring the real `_make_app` /
# `_reset_init` shape (module flag + lru_cache'd settings + create_app).
# ---------------------------------------------------------------------------

_initialized = False


def _reset_initialized() -> None:
    global _initialized
    _initialized = False


@lru_cache(maxsize=1)
def _get_settings() -> MCPSettings:
    return MCPSettings()  # reads TRANSPORT / MCP_HTTP_ACCESS_TOKEN from env


def create_app() -> object:
    global _initialized
    _initialized = True
    settings = _get_settings()
    mcp = FastMCP("fixture-test")
    add_health_route(mcp, "fixture-test")
    token = (
        settings.mcp_http_access_token.get_secret_value()
        if settings.mcp_http_access_token
        else None
    )
    return create_http_app(mcp, auth_token=token)


_fx = make_http_transport_fixtures(
    create_app=create_app,
    reset_fns=[_reset_initialized],
    lru_caches=[_get_settings],
)
make_app = _fx.make_app
app = _fx.app
client = _fx.client
reset = _fx.reset


class TestResetLruCaches:
    def test_clears_cache(self) -> None:
        calls: list[int] = []

        @lru_cache
        def f() -> int:
            calls.append(1)
            return len(calls)

        assert f() == 1
        assert f() == 1  # cached, body not re-run
        reset_lru_caches(f)
        assert f() == 2  # recomputed after clear

    def test_ignores_non_cache_callables(self) -> None:
        reset_lru_caches(lambda: None, 42, None)  # must not raise


class TestBearerHeaders:
    def test_format(self) -> None:
        assert bearer_headers("abc") == {"Authorization": "Bearer abc"}


class TestHttpTransportFixtures:
    def test_app_fixture_builds_callable_asgi_app(self, app: object) -> None:
        assert callable(app)

    @pytest.mark.anyio
    async def test_client_fixture_hits_health(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["service"] == "fixture-test"

    @pytest.mark.anyio
    async def test_make_app_access_token_is_enforced(self, make_app) -> None:
        application = make_app(access_token="secret")
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # /health always bypasses auth
            assert (await c.get("/health")).status_code == 200
            # a protected path rejects a missing or wrong token
            assert (await c.get("/needs-auth")).status_code == 401
            assert (await c.get("/needs-auth", headers=bearer_headers("wrong"))).status_code == 401
            # ...the configured token passes auth (404 from the router, not 401)
            ok = await c.get("/needs-auth", headers=bearer_headers("secret"))
            assert ok.status_code == 404

    def test_reset_fixture_resets_module_state(self, reset: None) -> None:
        # The reset fixture runs the reset_fns (incl. _reset_initialized) first.
        assert _initialized is False
