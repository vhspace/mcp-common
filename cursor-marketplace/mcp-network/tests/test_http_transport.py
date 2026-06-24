"""Tests for HTTP transport: health endpoint, ASGI factory, auth middleware, CORS."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def _make_app(access_token: str | None = None):
    """Build a test ASGI app via create_app() with env-based config."""
    import mcp_network.server as mod

    env = {"TRANSPORT": "http"}
    if access_token is not None:
        env["MCP_HTTP_ACCESS_TOKEN"] = access_token
    with patch.dict("os.environ", env, clear=False):
        mod.settings = mod.Settings()
        app = mod.create_app()
    return app


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.anyio
    async def test_health_returns_200(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["service"] == "mcp-network"

    @pytest.mark.anyio
    async def test_liveness_always_200(self):
        """?probe=liveness always returns 200 regardless of downstream health."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health?probe=liveness")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# ASGI factory
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_create_app_returns_asgi_callable(self):
        app = _make_app()
        assert callable(app)

    @pytest.mark.anyio
    async def test_health_via_asgi(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "mcp-network"

    @pytest.mark.anyio
    async def test_cors_preflight_allows_mcp_session_header(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.options(
                "/mcp",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "mcp-session-id",
                },
            )
            assert resp.headers.get("access-control-allow-origin") == "*"
            allowed = resp.headers.get("access-control-allow-headers", "")
            assert "mcp-session-id" in allowed.lower()

    @pytest.mark.anyio
    async def test_cors_expose_headers_on_response(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )
            assert resp.status_code == 200
            exposed = resp.headers.get("access-control-expose-headers", "")
            assert "mcp-session-id" in exposed.lower()


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.mark.anyio
    async def test_no_auth_when_token_not_configured(self):
        app = _make_app(access_token=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_auth_configured_health_still_accessible(self):
        app = _make_app(access_token="my-secret")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
