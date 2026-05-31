"""Tests for HTTP transport: health endpoint, ASGI factory, auth middleware, CORS."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from weka_mcp.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_init():
    """Reset the module-level _initialized flag and middleware between tests."""
    import weka_mcp.server as mod

    original_init = mod._initialized
    original_middleware = mod.mcp.middleware[:]
    mod._initialized = False
    yield
    mod._initialized = original_init
    mod.mcp.middleware[:] = original_middleware


def _make_app(access_token: str | None = None):
    """Build a test ASGI app via create_app() with mocked Weka client."""
    import weka_mcp.server as mod

    mod._initialized = False
    env = {
        "WEKA_HOST": "https://weka.test:14000",
        "WEKA_USERNAME": "admin",
        "WEKA_PASSWORD": "fake-pass",
        "TRANSPORT": "http",
    }
    if access_token is not None:
        env["MCP_HTTP_ACCESS_TOKEN"] = access_token
    with (
        patch.dict("os.environ", env, clear=False),
        patch("weka_mcp.site_manager.WekaRestClient"),
    ):
        app = create_app()
        active_key = mod.sites.active_key
        if active_key and active_key not in mod.sites._clients:
            mod.sites._clients[active_key] = MagicMock()
    return app


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.anyio
    async def test_health_returns_200_when_healthy(self, _reset_init):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["service"] == "weka-mcp"

    @pytest.mark.anyio
    async def test_health_returns_503_when_degraded(self, _reset_init):
        import weka_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

            def close(self):
                pass

        app = _make_app()
        active_key = mod.sites.active_key
        mod.sites._clients[active_key] = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
                assert resp.status_code == 503
                body = resp.json()
                assert body["status"] == "degraded"
                assert body["checks"]["weka_cluster"]["status"] == "error"
        finally:
            del mod.sites._clients[active_key]

    @pytest.mark.anyio
    async def test_health_ok_when_no_active_site(self, _reset_init):
        import weka_mcp.server as mod

        app = _make_app()
        original_key = mod.sites._active_key
        mod.sites._active_key = None
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
                assert resp.status_code == 200
        finally:
            mod.sites._active_key = original_key

    @pytest.mark.anyio
    async def test_liveness_always_200(self, _reset_init):
        """?probe=liveness returns 200 even when Weka is unreachable."""
        import weka_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

            def close(self):
                pass

        app = _make_app()
        active_key = mod.sites.active_key
        mod.sites._clients[active_key] = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health?probe=liveness")
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "ok"
        finally:
            del mod.sites._clients[active_key]

    @pytest.mark.anyio
    async def test_readiness_checks_weka(self, _reset_init):
        """Default probe (readiness) returns 503 when Weka is down."""
        import weka_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

            def close(self):
                pass

        app = _make_app()
        active_key = mod.sites.active_key
        mod.sites._clients[active_key] = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health?probe=readiness")
                assert resp.status_code == 503
        finally:
            del mod.sites._clients[active_key]


# ---------------------------------------------------------------------------
# ASGI factory
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_create_app_returns_asgi_callable(self, _reset_init):
        app = _make_app()
        assert callable(app)

    @pytest.mark.anyio
    async def test_health_via_asgi(self, _reset_init):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "weka-mcp"

    @pytest.mark.anyio
    async def test_cors_preflight_allows_mcp_session_header(self, _reset_init):
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
    async def test_cors_expose_headers_on_response(self, _reset_init):
        """expose-headers only appears on actual responses, not preflight."""
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
# Auth middleware (integration tests)
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.mark.anyio
    async def test_no_auth_when_token_not_configured(self, _reset_init):
        app = _make_app(access_token=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_auth_configured_health_still_accessible(self, _reset_init):
        app = _make_app(access_token="my-secret")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
