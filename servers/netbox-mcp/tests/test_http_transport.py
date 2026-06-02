"""Tests for HTTP transport: health endpoint, ASGI factory, auth middleware, CORS."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from netbox_mcp.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_init():
    """Reset the module-level _initialized flag and middleware between tests."""
    import netbox_mcp.server as mod

    original_init = mod._initialized
    original_middleware = mod.mcp.middleware[:]
    mod._initialized = False
    yield
    mod._initialized = original_init
    mod.mcp.middleware[:] = original_middleware


def _make_app(access_token: str | None = None):
    """Build a test ASGI app via create_app() with mocked NetBox client."""
    import netbox_mcp.server as mod

    mod._initialized = False
    env = {
        "NETBOX_URL": "https://netbox.test/",
        "NETBOX_TOKEN": "fake-token",
        "TRANSPORT": "http",
    }
    if access_token is not None:
        env["MCP_HTTP_ACCESS_TOKEN"] = access_token
    with (
        patch.dict("os.environ", env, clear=False),
        patch("netbox_mcp.server.NetBoxRestClient"),
    ):
        app = create_app()
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
            assert body["service"] == "netbox-mcp"

    @pytest.mark.anyio
    async def test_health_returns_503_when_degraded(self, _reset_init):
        import netbox_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

        app = _make_app()
        original = mod.netbox
        mod.netbox = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
                assert resp.status_code == 503
                body = resp.json()
                assert body["status"] == "degraded"
                assert body["checks"]["netbox_api"]["status"] == "error"
        finally:
            mod.netbox = original

    @pytest.mark.anyio
    async def test_health_ok_when_no_client(self, _reset_init):
        import netbox_mcp.server as mod

        app = _make_app()
        original = mod.netbox
        mod.netbox = None
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
                assert resp.status_code == 200
        finally:
            mod.netbox = original

    @pytest.mark.anyio
    async def test_liveness_always_200(self, _reset_init):
        """?probe=liveness returns 200 even when NetBox is unreachable."""
        import netbox_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

        app = _make_app()
        original = mod.netbox
        mod.netbox = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health?probe=liveness")
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "ok"
        finally:
            mod.netbox = original

    @pytest.mark.anyio
    async def test_readiness_checks_netbox(self, _reset_init):
        """Default probe (readiness) returns 503 when NetBox is down."""
        import netbox_mcp.server as mod

        class FailClient:
            def get(self, *a, **kw):
                raise ConnectionError("down")

        app = _make_app()
        original = mod.netbox
        mod.netbox = FailClient()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health?probe=readiness")
                assert resp.status_code == 503
        finally:
            mod.netbox = original


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
            assert data["service"] == "netbox-mcp"

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
