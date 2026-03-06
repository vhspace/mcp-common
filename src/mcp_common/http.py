"""Shared HTTP transport utilities for MCP servers."""

from __future__ import annotations

import hmac
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_common.auth import HttpAccessTokenAuth


class _BearerTokenMiddleware:
    """Starlette-compatible ASGI middleware for bearer-token auth.

    Skips authentication for ``/health`` and ``OPTIONS`` requests.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if path == "/health" or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        api_key = headers.get(b"x-api-key", b"").decode()

        ok = False
        if api_key and hmac.compare_digest(api_key, self._token):
            ok = True
        elif auth.lower().startswith("bearer "):
            candidate = auth.split(" ", 1)[1].strip()
            if hmac.compare_digest(candidate, self._token):
                ok = True

        if not ok:
            resp = Response("Unauthorized", status_code=401)
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


def add_health_route(
    mcp: Any,
    service_name: str,
    health_check_fn: Any = None,
) -> None:
    """Add a ``/health`` endpoint to an MCP server.

    Supports Kubernetes-style liveness and readiness probes via the ``?probe=``
    query parameter.  Liveness always returns 200; readiness runs the optional
    *health_check_fn* and returns 503 if any check reports an error.

    Args:
        mcp: FastMCP instance.
        service_name: Name included in the health response.
        health_check_fn: Optional async callable returning ``dict[str, Any]``
            where each value may be a dict with a ``"status"`` key.
    """

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        probe = request.query_params.get("probe", "readiness")

        result: dict[str, Any] = {"status": "ok", "service": service_name}

        if probe == "liveness":
            return JSONResponse(result)

        if health_check_fn:
            checks = await health_check_fn()
            result["checks"] = checks
            if any(v.get("status") == "error" for v in checks.values() if isinstance(v, dict)):
                result["status"] = "degraded"
                return JSONResponse(result, status_code=503)

        return JSONResponse(result)


def create_http_app(
    mcp: Any,
    path: str = "/mcp",
    cors_origins: list[str] | None = None,
    auth_token: str | None = None,
    stateless_http: bool = True,
) -> Any:
    """Create a production-ready ASGI app from a FastMCP instance.

    Configures CORS (including ``mcp-session-id`` headers required by clients
    such as Cursor) and optional bearer-token authentication.

    Args:
        mcp: FastMCP instance.
        path: URL path to mount the MCP endpoint on.
        cors_origins: Allowed CORS origins.  Defaults to ``["*"]``.
        auth_token: If provided, all non-health/OPTIONS requests require a
            matching ``Authorization: Bearer`` or ``X-API-Key`` header.
        stateless_http: Disable server-side session state so the server can
            run behind a load balancer without session affinity. Passed
            through to ``FastMCP.http_app()``.

    Returns:
        A Starlette ASGI application.
    """
    from starlette.middleware.cors import CORSMiddleware

    if hasattr(mcp, "http_app"):
        app = mcp.http_app(path=path, stateless_http=stateless_http)
    elif hasattr(mcp, "streamable_http_app"):
        app = mcp.streamable_http_app()
    else:
        raise AttributeError(
            "MCP instance has neither http_app() nor streamable_http_app(). "
            "Ensure you are using a compatible FastMCP version."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*", "mcp-session-id", "mcp-protocol-version"],
        expose_headers=["mcp-session-id"],
    )

    if auth_token:
        app.add_middleware(_BearerTokenMiddleware, token=auth_token)
        if hasattr(mcp, "middleware"):
            mcp.middleware.append(HttpAccessTokenAuth(auth_token))

    return app
