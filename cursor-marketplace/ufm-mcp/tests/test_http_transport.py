"""Tests for HTTP transport: health endpoint, ASGI factory, auth middleware, CORS."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def _reset_module():
    """Reset module-level singletons between tests."""
    import ufm_mcp.server as mod

    mod._initialized = False
    mod._base_settings = None


def _make_app(access_token: str | None = None):
    """Build a test ASGI app via create_app() with mocked UFM client."""
    _reset_module()
    env = {
        "UFM_URL": "https://ufm.test/",
        "UFM_TOKEN": "fake-token",
        "TRANSPORT": "http",
    }
    if access_token is not None:
        env["MCP_HTTP_ACCESS_TOKEN"] = access_token
    with patch.dict("os.environ", env, clear=False):
        from ufm_mcp.server import create_app

        app = create_app()
    return app


@pytest.fixture(autouse=True)
def _clean_module():
    """Reset module state before and after each test."""
    _reset_module()
    yield
    _reset_module()


class TestHealthEndpoint:
    @pytest.mark.anyio
    async def test_health_returns_status(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
            body = resp.json()
            assert body["status"] in ("ok", "degraded")
            assert body["service"] == "ufm-mcp"

    @pytest.mark.anyio
    async def test_liveness_always_200(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health?probe=liveness")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"


class TestCreateApp:
    def test_create_app_returns_asgi_callable(self):
        app = _make_app()
        assert callable(app)

    @pytest.mark.anyio
    async def test_health_via_asgi(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert data["service"] == "ufm-mcp"

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


class TestAuthMiddleware:
    @pytest.mark.anyio
    async def test_no_auth_when_token_not_configured(self):
        app = _make_app(access_token=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)

    @pytest.mark.anyio
    async def test_auth_configured_health_still_accessible(self):
        app = _make_app(access_token="my-secret")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
