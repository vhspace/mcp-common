"""Shared HTTP transport utilities for MCP servers."""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, NamedTuple, Protocol

import httpx
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
from mcp_common.version import get_version


def user_agent(component: str | None = None) -> str:
    """Return a stable, explicit outbound HTTP ``User-Agent`` string.

    Every MCP HTTP client MUST send an explicit ``User-Agent``: the default
    ``Python-urllib/*`` UA is banned by the Cloudflare WAF in front of Together
    infrastructure (e.g. ``i.together.ai`` / NetBox and ``api.together.xyz``),
    which returns ``403`` (CF Error 1010, ``browser_signature_banned``). This
    helper returns a non-default UA derived from the real installed
    ``mcp-common`` version so the token never goes stale.

    Args:
        component: Optional label identifying the calling client/component. When
            provided it is prepended to the base token.

    Returns:
        ``"mcp-common/<version>"`` (e.g. ``"mcp-common/0.25.0"``), or
        ``"<component> mcp-common/<version>"`` when *component* is given.
    """
    base = f"mcp-common/{get_version('mcp-common')}"
    if component:
        return f"{component} {base}"
    return base


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
                    error_fingerprint=compute_http_error_fingerprint(status, scope.get("path")),
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


# ---------------------------------------------------------------------------
# Outbound HTTP client base (#88)
#
# A reusable httpx client with retry-on-(429/502/503/504), exponential backoff,
# ``Retry-After`` handling, and standardized error wrapping. Replaces the
# near-identical retry loops duplicated across MCP-specific REST clients
# (awx-mcp, weka-mcp, ufm-mcp, ...).
# ---------------------------------------------------------------------------

_DEFAULT_RETRY_STATUS_CODES: tuple[int, ...] = (429, 502, 503, 504)
_ERROR_BODY_LIMIT = 2048


class HttpClientError(Exception):
    """Raised when an outbound HTTP request ultimately fails.

    Wraps both non-success HTTP responses (after retries are exhausted) and
    transport-level failures (connect/read/timeout errors) in a single,
    predictable exception type carrying structured context for logging and
    remediation.

    Attributes:
        message: Human-readable summary.
        method: HTTP method of the originating request (upper-case), if known.
        url: Target URL with the query string stripped (avoids leaking tokens
            passed as query params), if known.
        status_code: HTTP status code, or ``None`` for transport-level errors.
        body: Response body text (truncated to a sane limit) when available.
    """

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body

    @classmethod
    def from_response(cls, response: httpx.Response) -> HttpClientError:
        """Build an error from a non-success :class:`httpx.Response`."""
        body = response.text or ""
        if len(body) > _ERROR_BODY_LIMIT:
            body = body[:_ERROR_BODY_LIMIT] + "..."
        request = response.request
        method = request.method if request is not None else None
        url = _strip_query(str(request.url)) if request is not None else None
        return cls(
            f"HTTP {response.status_code} for {method or '?'} {url or '?'}",
            method=method,
            url=url,
            status_code=response.status_code,
            body=body or None,
        )


@dataclass(frozen=True)
class HttpClientConfig:
    """Retry / backoff policy for the retrying HTTP clients.

    Attributes:
        retry_status_codes: HTTP status codes that trigger a retry.
        max_retries: Maximum number of retries *after* the initial attempt
            (so up to ``max_retries + 1`` total requests).
        backoff_base: Base seconds for exponential backoff
            (``backoff_base * 2 ** attempt``).
        backoff_max: Upper bound (seconds) on any single backoff sleep; also
            caps an honored ``Retry-After`` to avoid pathological waits.
        respect_retry_after: Honor a ``Retry-After`` response header when present.
        retry_on_transport_errors: Retry on :class:`httpx.TransportError`
            (connect/read/timeout) in addition to retryable status codes.
    """

    retry_status_codes: tuple[int, ...] = _DEFAULT_RETRY_STATUS_CODES
    max_retries: int = 5
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    respect_retry_after: bool = True
    retry_on_transport_errors: bool = True


def _strip_query(url: str) -> str:
    """Return *url* without its query string (avoids logging secret params)."""
    return url.split("?", 1)[0]


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) to seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_dt is None:
        return None
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    return max(0.0, (retry_dt - datetime.now(UTC)).total_seconds())


def _backoff_delay(attempt: int, config: HttpClientConfig, retry_after: float | None) -> float:
    """Seconds to sleep before *attempt*+1, honoring Retry-After then backoff."""
    if retry_after is not None:
        return min(retry_after, config.backoff_max)
    return min(config.backoff_max, config.backoff_base * (2.0**attempt))


def _retry_after_seconds(response: httpx.Response, config: HttpClientConfig) -> float | None:
    if not config.respect_retry_after:
        return None
    return _parse_retry_after(response.headers.get("Retry-After"))


def _log_retry(
    logger: logging.Logger | None,
    method: str,
    url: str,
    attempt: int,
    *,
    status: int | None = None,
    delay: float | None = None,
    error: BaseException | None = None,
) -> None:
    if logger is None:
        return
    log_trace_event(
        logger,
        "retrying outbound HTTP request",
        exc_info=error if error is not None else False,
        http_status=status,
        method=method.upper(),
        path=_strip_query(url),
        attempt=attempt,
        retry_delay_s=round(delay, 3) if delay is not None else None,
    )


def _json_or_raise(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise HttpClientError.from_response(response)
    return response.json()


def _with_default_user_agent(
    headers: Mapping[str, str] | None, component: str | None
) -> dict[str, str]:
    """Return *headers* ensuring an explicit ``User-Agent`` is set (#121).

    The default ``python-httpx/*`` / ``Python-urllib/*`` User-Agent is banned by
    the Cloudflare WAF in front of Together infrastructure (403, CF Error 1010).
    Every outbound client therefore sends the standardized :func:`user_agent`
    token by default. An explicit caller-supplied ``User-Agent`` header (any
    casing) is always preserved.
    """
    resolved = dict(headers) if headers else {}
    if not any(k.lower() == "user-agent" for k in resolved):
        resolved["User-Agent"] = user_agent(component)
    return resolved


class RetryingHttpxClient:
    """Synchronous httpx client with retry, backoff and error wrapping.

    Example::

        with RetryingHttpxClient("https://api.example.com", auth=("user", "pw")) as c:
            data = c.get_json("/v1/items", params={"limit": 10})

    The underlying :class:`httpx.Client` is created eagerly; use the client as a
    context manager (or call :meth:`close`) to release connections.

    By default the client sends the standardized :func:`user_agent` header
    (#121) so requests survive the Cloudflare WAF UA ban; pass *component* to
    label it (``"<component> mcp-common/<version>"``), or supply an explicit
    ``User-Agent`` in *headers* to override.
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: httpx.Auth | tuple[str, str] | None = None,
        config: HttpClientConfig | None = None,
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
        component: str | None = None,
        follow_redirects: bool = True,
        logger: logging.Logger | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or HttpClientConfig()
        self._logger = logger
        self._client = httpx.Client(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            headers=_with_default_user_agent(headers, component),
            follow_redirects=follow_redirects,
            transport=transport,
        )

    @property
    def client(self) -> httpx.Client:
        """The underlying :class:`httpx.Client` (for advanced use)."""
        return self._client

    def __enter__(self) -> RetryingHttpxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, retrying transient failures per :attr:`config`.

        Returns the final :class:`httpx.Response` (which may carry a retryable
        status code if retries were exhausted). :meth:`get_json` /
        :meth:`post_json` translate non-success responses into
        :class:`HttpClientError`.
        """
        cfg = self.config
        attempt = 0
        while True:
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if cfg.retry_on_transport_errors and attempt < cfg.max_retries:
                    delay = _backoff_delay(attempt, cfg, None)
                    _log_retry(self._logger, method, path, attempt, delay=delay, error=exc)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise HttpClientError(
                    f"transport error for {method.upper()} {_strip_query(path)}: {exc}",
                    method=method.upper(),
                    url=_strip_query(path),
                ) from exc
            if response.status_code in cfg.retry_status_codes and attempt < cfg.max_retries:
                delay = _backoff_delay(attempt, cfg, _retry_after_seconds(response, cfg))
                _log_retry(
                    self._logger, method, path, attempt, status=response.status_code, delay=delay
                )
                response.close()
                time.sleep(delay)
                attempt += 1
                continue
            return response

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        """GET *path* and return parsed JSON, raising on non-success status."""
        return _json_or_raise(self.request_with_retry("GET", path, params=params, **kwargs))

    def post_json(self, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        """POST JSON to *path* and return parsed JSON, raising on non-success."""
        return _json_or_raise(self.request_with_retry("POST", path, json=json, **kwargs))


class AsyncRetryingHttpxClient:
    """Asynchronous counterpart of :class:`RetryingHttpxClient`.

    Sends the standardized :func:`user_agent` header by default like the sync
    client (#121); use *component* to label it or override via *headers*.

    Example::

        async with AsyncRetryingHttpxClient("https://api.example.com") as c:
            data = await c.get_json("/v1/items")
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: httpx.Auth | tuple[str, str] | None = None,
        config: HttpClientConfig | None = None,
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
        component: str | None = None,
        follow_redirects: bool = True,
        logger: logging.Logger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or HttpClientConfig()
        self._logger = logger
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            headers=_with_default_user_agent(headers, component),
            follow_redirects=follow_redirects,
            transport=transport,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying :class:`httpx.AsyncClient` (for advanced use)."""
        return self._client

    async def __aenter__(self) -> AsyncRetryingHttpxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Async variant of :meth:`RetryingHttpxClient.request_with_retry`."""
        cfg = self.config
        attempt = 0
        while True:
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if cfg.retry_on_transport_errors and attempt < cfg.max_retries:
                    delay = _backoff_delay(attempt, cfg, None)
                    _log_retry(self._logger, method, path, attempt, delay=delay, error=exc)
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise HttpClientError(
                    f"transport error for {method.upper()} {_strip_query(path)}: {exc}",
                    method=method.upper(),
                    url=_strip_query(path),
                ) from exc
            if response.status_code in cfg.retry_status_codes and attempt < cfg.max_retries:
                delay = _backoff_delay(attempt, cfg, _retry_after_seconds(response, cfg))
                _log_retry(
                    self._logger, method, path, attempt, status=response.status_code, delay=delay
                )
                await response.aclose()
                await asyncio.sleep(delay)
                attempt += 1
                continue
            return response

    async def get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        """GET *path* and return parsed JSON, raising on non-success status."""
        return _json_or_raise(await self.request_with_retry("GET", path, params=params, **kwargs))

    async def post_json(self, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        """POST JSON to *path* and return parsed JSON, raising on non-success."""
        return _json_or_raise(await self.request_with_retry("POST", path, json=json, **kwargs))


# ---------------------------------------------------------------------------
# Conditional GET / ETag helpers (#83)
#
# A tiny, composable primitive for the "refresh a small reference table every
# N seconds" pattern: send `If-None-Match` (or `If-Modified-Since`) so unchanged
# data comes back as a zero-body 304. Deliberately NOT a generic HTTP cache —
# the caller owns the body cache. Works structurally against either a
# `requests.Response`/`requests.Session` or an `httpx` equivalent (no new
# runtime deps; typed via Protocols so a future httpx variant drops in).
# ---------------------------------------------------------------------------


class _HeadersLike(Protocol):
    def get(self, key: str, default: str | None = ..., /) -> str | None: ...


class _ResponseLike(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> _HeadersLike: ...


class _SessionLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> _ResponseLike: ...


@dataclass
class ETagStore:
    """In-memory record of validators (ETag / Last-Modified) keyed by cache key.

    The *key* is caller-defined (commonly ``f"{method} {url}"`` or just the
    endpoint). The simple ETag path is the default; ``If-Modified-Since`` /
    ``Last-Modified`` fallback is gated behind ``use_last_modified=True`` so the
    common case stays trivial.
    """

    use_last_modified: bool = False
    _etags: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _last_modified: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def get_etag(self, key: str) -> str | None:
        """Return the stored ETag for *key*, if any."""
        return self._etags.get(key)

    def get_last_modified(self, key: str) -> str | None:
        """Return the stored ``Last-Modified`` value for *key* (when enabled)."""
        if not self.use_last_modified:
            return None
        return self._last_modified.get(key)

    def remember(
        self, key: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> None:
        """Store validators for *key*. ``None`` values are ignored (idempotent)."""
        if etag is not None:
            self._etags[key] = etag
        if last_modified is not None and self.use_last_modified:
            self._last_modified[key] = last_modified

    def forget(self, key: str) -> None:
        """Drop any stored validators for *key*."""
        self._etags.pop(key, None)
        self._last_modified.pop(key, None)

    def clear(self) -> None:
        """Drop all stored validators."""
        self._etags.clear()
        self._last_modified.clear()


class ConditionalGetResult(NamedTuple):
    """Result of :func:`conditional_get`.

    Unpacks as ``(status, response)`` to match the documented signature; use
    :attr:`not_modified` for the clear "keep your cached body" signal.
    """

    status: int
    response: Any

    @property
    def not_modified(self) -> bool:
        """True when the server returned ``304 Not Modified``."""
        return self.status == 304


def apply_conditional_headers(
    store: ETagStore, key: str, headers: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return a copy of *headers* with the conditional validator set for *key*.

    Sets ``If-None-Match`` when an ETag is known; otherwise falls back to
    ``If-Modified-Since`` when the store has ``use_last_modified=True`` and a
    ``Last-Modified`` value is known. When nothing is known, the headers are
    returned unchanged (so the first request emits no conditional header).
    """
    result: dict[str, str] = dict(headers) if headers else {}
    etag = store.get_etag(key)
    if etag:
        result["If-None-Match"] = etag
    else:
        last_modified = store.get_last_modified(key)
        if last_modified:
            result["If-Modified-Since"] = last_modified
    return result


def record_response(store: ETagStore, key: str, response: _ResponseLike) -> None:
    """Capture the ``ETag`` (and optionally ``Last-Modified``) from *response*.

    Idempotent when the headers are absent — an existing stored validator is
    left untouched rather than cleared.
    """
    etag = response.headers.get("ETag")
    last_modified = response.headers.get("Last-Modified") if store.use_last_modified else None
    store.remember(key, etag=etag, last_modified=last_modified)


def conditional_get(
    session: _SessionLike,
    url: str,
    *,
    key: str,
    store: ETagStore,
    headers: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> ConditionalGetResult:
    """Wrap a single ``session.get`` with conditional-request plumbing.

    Applies the known validator for *key*, performs the GET, and — on any
    non-304 response — records the fresh ``ETag``/``Last-Modified``. On ``304``
    the caller keeps its existing body (see :attr:`ConditionalGetResult.not_modified`).
    The caller owns the body cache; this helper only manages the validators.
    """
    request_headers = apply_conditional_headers(store, key, headers)
    response = session.get(url, headers=request_headers, **kwargs)
    if response.status_code != 304:
        record_response(store, key, response)
    return ConditionalGetResult(response.status_code, response)
