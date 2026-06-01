"""Tests for the retrying outbound HTTP client (#88)."""

from __future__ import annotations

import httpx
import pytest

from mcp_common.http import (
    AsyncRetryingHttpxClient,
    HttpClientConfig,
    HttpClientError,
    RetryingHttpxClient,
    _backoff_delay,
    _parse_retry_after,
)


def _record_transport(handler):
    """Wrap a per-request handler, recording every request issued."""
    calls: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(len(calls) - 1, request)

    return httpx.MockTransport(_handle), calls


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch time.sleep so retries don't actually wait; record the delays."""
    delays: list[float] = []
    monkeypatch.setattr("mcp_common.http.time.sleep", lambda s: delays.append(s))
    return delays


@pytest.fixture
def no_async_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def _fake(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("mcp_common.http.asyncio.sleep", _fake)
    return delays


# ---------------------------------------------------------------------------
# Backoff / Retry-After unit helpers
# ---------------------------------------------------------------------------


class TestBackoffHelpers:
    def test_exponential_backoff_grows_and_caps(self) -> None:
        cfg = HttpClientConfig(backoff_base=0.5, backoff_max=30.0)
        assert _backoff_delay(0, cfg, None) == 0.5
        assert _backoff_delay(1, cfg, None) == 1.0
        assert _backoff_delay(2, cfg, None) == 2.0
        assert _backoff_delay(10, cfg, None) == 30.0  # capped

    def test_retry_after_takes_precedence_but_is_capped(self) -> None:
        cfg = HttpClientConfig(backoff_max=30.0)
        assert _backoff_delay(0, cfg, 5.0) == 5.0
        assert _backoff_delay(0, cfg, 120.0) == 30.0  # capped at backoff_max

    def test_parse_retry_after_seconds(self) -> None:
        assert _parse_retry_after("7") == 7.0

    def test_parse_retry_after_http_date(self) -> None:
        # A date far in the past yields 0 (never negative).
        assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0

    def test_parse_retry_after_garbage_is_none(self) -> None:
        assert _parse_retry_after("not-a-date") is None
        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class TestRetryingHttpxClient:
    def test_get_json_success_first_try(self) -> None:
        transport, calls = _record_transport(lambda i, req: httpx.Response(200, json={"ok": True}))
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            assert c.get_json("/v1/thing") == {"ok": True}
        assert len(calls) == 1

    def test_retries_then_succeeds(self, no_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            if i < 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"done": 1})

        transport, calls = _record_transport(handler)
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            assert c.get_json("/x") == {"done": 1}
        assert len(calls) == 3  # 2 failures + 1 success
        assert no_sleep == [0.5, 1.0]  # exponential backoff between attempts

    def test_retry_exhaustion_raises_with_context(self, no_sleep: list[float]) -> None:
        transport, calls = _record_transport(
            lambda i, req: httpx.Response(503, text="upstream down")
        )
        cfg = HttpClientConfig(max_retries=3, backoff_base=0.1)
        with RetryingHttpxClient("https://api.test", config=cfg, transport=transport) as c:
            with pytest.raises(HttpClientError) as exc_info:
                c.get_json("/x")
        assert len(calls) == 4  # initial + 3 retries
        err = exc_info.value
        assert err.status_code == 503
        assert err.method == "GET"
        assert err.url == "https://api.test/x"
        assert err.body == "upstream down"

    def test_non_retryable_status_not_retried(self, no_sleep: list[float]) -> None:
        transport, calls = _record_transport(lambda i, req: httpx.Response(404, text="nope"))
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            with pytest.raises(HttpClientError) as exc_info:
                c.get_json("/missing")
        assert len(calls) == 1  # 404 is not retryable
        assert no_sleep == []
        assert exc_info.value.status_code == 404

    def test_request_with_retry_returns_response_without_raising(
        self, no_sleep: list[float]
    ) -> None:
        transport, _ = _record_transport(lambda i, req: httpx.Response(404))
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            resp = c.request_with_retry("GET", "/missing")
        assert resp.status_code == 404  # raw access does not raise

    def test_retry_after_header_honored(self, no_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            if i == 0:
                return httpx.Response(429, headers={"Retry-After": "3"})
            return httpx.Response(200, json={})

        transport, _ = _record_transport(handler)
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            c.get_json("/rate-limited")
        assert no_sleep == [3.0]  # honored Retry-After, not the 0.5 backoff base

    def test_transport_error_retried_then_succeeds(self, no_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            if i == 0:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(200, json={"ok": 1})

        transport, calls = _record_transport(handler)
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            assert c.get_json("/flaky") == {"ok": 1}
        assert len(calls) == 2

    def test_transport_error_exhaustion_wraps(self, no_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=req)

        transport, _ = _record_transport(handler)
        cfg = HttpClientConfig(max_retries=2, backoff_base=0.1)
        with RetryingHttpxClient("https://api.test", config=cfg, transport=transport) as c:
            with pytest.raises(HttpClientError) as exc_info:
                c.get_json("/down")
        assert exc_info.value.status_code is None  # transport-level error
        assert "transport error" in exc_info.value.message

    def test_post_json(self) -> None:
        seen: dict[str, object] = {}

        def handler(i: int, req: httpx.Request) -> httpx.Response:
            seen["body"] = req.content
            return httpx.Response(201, json={"created": True})

        transport, _ = _record_transport(handler)
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            assert c.post_json("/items", json={"name": "x"}) == {"created": True}
        assert b'"name"' in seen["body"]  # type: ignore[operator]

    def test_custom_retry_status_codes(self, no_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            if i == 0:
                return httpx.Response(418)  # teapot, custom-retryable
            return httpx.Response(200, json={})

        transport, calls = _record_transport(handler)
        cfg = HttpClientConfig(retry_status_codes=(418,))
        with RetryingHttpxClient("https://api.test", config=cfg, transport=transport) as c:
            c.get_json("/teapot")
        assert len(calls) == 2

    def test_error_body_is_truncated(self, no_sleep: list[float]) -> None:
        big = "x" * 5000
        transport, _ = _record_transport(lambda i, req: httpx.Response(400, text=big))
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            with pytest.raises(HttpClientError) as exc_info:
                c.get_json("/big")
        assert exc_info.value.body is not None
        assert len(exc_info.value.body) < len(big)
        assert exc_info.value.body.endswith("...")


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class TestAsyncRetryingHttpxClient:
    @pytest.mark.anyio
    async def test_get_json_success(self) -> None:
        transport, calls = _record_transport(lambda i, req: httpx.Response(200, json={"ok": True}))
        async with AsyncRetryingHttpxClient("https://api.test", transport=transport) as c:
            assert await c.get_json("/v1/thing") == {"ok": True}
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_retries_then_succeeds(self, no_async_sleep: list[float]) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            if i < 2:
                return httpx.Response(502)
            return httpx.Response(200, json={"done": 1})

        transport, calls = _record_transport(handler)
        async with AsyncRetryingHttpxClient("https://api.test", transport=transport) as c:
            assert await c.get_json("/x") == {"done": 1}
        assert len(calls) == 3
        assert no_async_sleep == [0.5, 1.0]

    @pytest.mark.anyio
    async def test_exhaustion_raises(self, no_async_sleep: list[float]) -> None:
        transport, _ = _record_transport(lambda i, req: httpx.Response(503))
        cfg = HttpClientConfig(max_retries=2, backoff_base=0.1)
        async with AsyncRetryingHttpxClient(
            "https://api.test", config=cfg, transport=transport
        ) as c:
            with pytest.raises(HttpClientError) as exc_info:
                await c.post_json("/x", json={"a": 1})
        assert exc_info.value.status_code == 503
