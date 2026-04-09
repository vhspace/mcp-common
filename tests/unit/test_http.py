"""Tests for HTTP transport utilities (health route, ASGI factory)."""

from __future__ import annotations

import logging

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

from mcp_common.config import MCPSettings
from mcp_common.http import add_health_route, create_http_app
from mcp_common.logging import LOG_CHANNEL_ACCESS


@pytest.fixture
def fresh_mcp() -> FastMCP:
    return FastMCP("test-server")


class TestAddHealthRoute:
    @pytest.mark.anyio
    async def test_health_returns_200(self, fresh_mcp: FastMCP) -> None:
        add_health_route(fresh_mcp, "my-service")
        app = fresh_mcp.http_app(path="/mcp")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "my-service"

    @pytest.mark.anyio
    async def test_liveness_probe_always_200(self, fresh_mcp: FastMCP) -> None:
        async def failing_check() -> dict:
            return {"db": {"status": "error"}}

        add_health_route(fresh_mcp, "my-service", health_check_fn=failing_check)
        app = fresh_mcp.http_app(path="/mcp")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health?probe=liveness")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    @pytest.mark.anyio
    async def test_readiness_503_on_degraded(self, fresh_mcp: FastMCP) -> None:
        async def failing_check() -> dict:
            return {"db": {"status": "error"}}

        add_health_route(fresh_mcp, "my-service", health_check_fn=failing_check)
        app = fresh_mcp.http_app(path="/mcp")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["db"]["status"] == "error"

    @pytest.mark.anyio
    async def test_readiness_200_when_checks_pass(self, fresh_mcp: FastMCP) -> None:
        async def healthy_check() -> dict:
            return {"db": {"status": "ok"}}

        add_health_route(fresh_mcp, "my-service", health_check_fn=healthy_check)
        app = fresh_mcp.http_app(path="/mcp")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["db"]["status"] == "ok"


class TestCreateHttpApp:
    def test_returns_callable(self, fresh_mcp: FastMCP) -> None:
        app = create_http_app(fresh_mcp)
        assert callable(app)

    @pytest.mark.anyio
    async def test_cors_preflight_allows_mcp_session_id(self, fresh_mcp: FastMCP) -> None:
        add_health_route(fresh_mcp, "test-svc")
        app = create_http_app(fresh_mcp)

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
    async def test_expose_headers_on_response(self, fresh_mcp: FastMCP) -> None:
        add_health_route(fresh_mcp, "test-svc")
        app = create_http_app(fresh_mcp)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/health",
                headers={"Origin": "http://localhost:3000"},
            )

        assert resp.status_code == 200
        exposed = resp.headers.get("access-control-expose-headers", "")
        assert "mcp-session-id" in exposed.lower()

    @pytest.mark.anyio
    async def test_health_via_factory_app(self, fresh_mcp: FastMCP) -> None:
        add_health_route(fresh_mcp, "factory-svc")
        app = create_http_app(fresh_mcp)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "factory-svc"

    def test_auth_token_adds_middleware(self, fresh_mcp: FastMCP) -> None:
        before = len(fresh_mcp.middleware)
        create_http_app(fresh_mcp, auth_token="secret")
        assert len(fresh_mcp.middleware) == before + 1

    def test_no_auth_token_no_middleware(self, fresh_mcp: FastMCP) -> None:
        before = len(fresh_mcp.middleware)
        create_http_app(fresh_mcp)
        assert len(fresh_mcp.middleware) == before

    @pytest.mark.anyio
    async def test_http_access_logging_records_path_status_duration(
        self,
        fresh_mcp: FastMCP,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        add_health_route(fresh_mcp, "access-svc")
        access_logger = logging.getLogger("test-http-access")
        access_logger.propagate = True

        with caplog.at_level(logging.INFO):
            app = create_http_app(
                fresh_mcp,
                http_access_logging=True,
                access_logger=access_logger,
                request_id_header="x-request-id",
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/health",
                    headers={"X-Request-Id": "custom-req-id"},
                )

        assert resp.status_code == 200
        access_records = [
            r for r in caplog.records if getattr(r, "log_channel", None) == LOG_CHANNEL_ACCESS
        ]
        assert len(access_records) >= 1
        last = access_records[-1]
        assert last.path == "/health"
        assert last.status == 200
        assert last.request_id == "custom-req-id"
        assert last.method == "GET"
        assert isinstance(last.duration_ms, (int, float))
        assert last.duration_ms >= 0

    @pytest.mark.anyio
    async def test_settings_log_http_access_enables_middleware(
        self,
        fresh_mcp: FastMCP,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        add_health_route(fresh_mcp, "settings-svc")
        access_logger = logging.getLogger("test-settings-access")
        access_logger.propagate = True
        settings = MCPSettings(log_http_access=True)

        with caplog.at_level(logging.INFO):
            app = create_http_app(
                fresh_mcp,
                settings=settings,
                access_logger=access_logger,
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.get("/health")

        access_records = [
            r for r in caplog.records if getattr(r, "log_channel", None) == LOG_CHANNEL_ACCESS
        ]
        assert len(access_records) >= 1
