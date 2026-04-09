"""Shared HTTP transport utilities for MCP servers."""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_common.auth import HttpAccessTokenAuth
from mcp_common.config import MCPSettings
from mcp_common.logging import (
    compute_error_fingerprint,
    compute_http_error_fingerprint,
    log_access_event,
    log_trace_event,
)


def _get_header(scope: Scope, name_lower: str) -> str | None:
    want = name_lower.lower().encode("latin-1")
    for k, v in scope.get("headers", []):
        if k.lower() == want:
            return str(v.decode("latin-1"))
    return None


def _normalize_request_id_header(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        return "x-request-id"
    return normalized


class _AccessLogMiddleware:
    """ASGI middleware: request timing, access logs, optional trace on 5xx / exceptions."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        logger: logging.Logger,
        request_id_header: str,
        trace_server_errors: bool,
        trace_include_stack: bool,
        emit_request_id_response_header: bool,
    ) -> None:
        self.app = app
        self._logger = logger
        self._request_id_header = request_id_header.lower()
        self._trace_server_errors = trace_server_errors
        self._trace_include_stack = trace_include_stack
        self._emit_request_id = emit_request_id_response_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        req_id = _get_header(scope, self._request_id_header) or uuid.uuid4().hex
        status_holder: list[int | None] = [None]
        exc: BaseException | None = None

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            to_send: MutableMapping[str, Any] = message
            if message["type"] == "http.response.start":
                status_holder[0] = message["status"]
                if self._emit_request_id and req_id:
                    hdr = self._request_id_header.encode("latin-1")
                    raw_headers = list(message.get("headers", []))
                    if not any(k.lower() == hdr for k, _ in raw_headers):
                        raw_headers.append((hdr, req_id.encode("latin-1")))
                        to_send = {**dict(message), "headers": raw_headers}
            await send(to_send)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as err:
            exc = err
            if self._trace_server_errors:
                log_trace_event(
                    self._logger,
                    "unhandled exception during HTTP request",
                    exc_info=err,
                    capture_stack=self._trace_include_stack,
                    request_id=req_id,
                    path=scope.get("path"),
                    method=scope.get("method"),
                    error_fingerprint=compute_error_fingerprint(err),
                )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            status = status_holder[0]
            if status is None:
                status = 500 if exc is not None else 200
            log_access_event(
                self._logger,
                "http request completed",
                path=scope.get("path") or "",
                status=status,
                duration_ms=round(duration_ms, 3),
                request_id=req_id,
                method=scope.get("method") or "",
            )
            if self._trace_server_errors and exc is None and status is not None and status >= 500:
                log_trace_event(
                    self._logger,
                    "http response indicated server error",
                    exc_info=False,
                    http_status=status,
                    request_id=req_id,
                    path=scope.get("path"),
                    method=scope.get("method"),
                    error_fingerprint=compute_http_error_fingerprint(status, req_id),
                )


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

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
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
    *,
    settings: MCPSettings | None = None,
    http_access_logging: bool = False,
    access_logger: logging.Logger | None = None,
    request_id_header: str = "x-request-id",
    trace_http_server_errors: bool = True,
    trace_include_stack: bool = False,
    emit_request_id_response_header: bool = True,
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
        settings: Optional :class:`~mcp_common.config.MCPSettings`. When set and
            ``log_http_access`` is True, HTTP access logging is enabled using
            fields from settings (request id header, trace flags).
        http_access_logging: Explicit opt-in for access logging middleware
            (defaults ``False`` for backward compatibility). Also enabled when
            *settings* has ``log_http_access=True``.
        access_logger: Logger for access/trace lines; defaults to
            ``mcp_common.http.access``.
        request_id_header: Incoming header to read for correlation (falls back
            to a generated id). Ignored when *settings* supplies
            ``log_request_id_header``.
        trace_http_server_errors: Emit trace-channel logs on uncaught exceptions
            and HTTP status ``>= 500`` (unless disabled via *settings*).
        trace_include_stack: Pass ``capture_stack=True`` to trace logs on
            exceptions (expensive).
        emit_request_id_response_header: Mirror the resolved request id on the
            response when access logging is enabled.

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

    enable_access = http_access_logging or (settings is not None and settings.log_http_access)
    rid_header = _normalize_request_id_header(request_id_header)
    server_error_trace = trace_http_server_errors
    inc_stack = trace_include_stack
    if settings is not None:
        rid_header = _normalize_request_id_header(settings.log_request_id_header or rid_header)
        inc_stack = settings.log_trace_include_stack
        if not settings.log_trace_on_error:
            server_error_trace = False

    expose = ["mcp-session-id"]
    if enable_access and emit_request_id_response_header:
        expose.append(rid_header.lower())

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*", "mcp-session-id", "mcp-protocol-version"],
        expose_headers=expose,
    )

    if auth_token:
        app.add_middleware(_BearerTokenMiddleware, token=auth_token)
        if hasattr(mcp, "middleware"):
            mcp.middleware.append(HttpAccessTokenAuth(auth_token))

    if enable_access:
        log = access_logger or logging.getLogger("mcp_common.http.access")
        app.add_middleware(
            _AccessLogMiddleware,
            logger=log,
            request_id_header=rid_header,
            trace_server_errors=server_error_trace,
            trace_include_stack=inc_stack,
            emit_request_id_response_header=emit_request_id_response_header,
        )

    return app
