"""Tests for the retrying outbound HTTP client (#88)."""

from __future__ import annotations

import httpx
import pytest

from mcpanvil.http import (
    AsyncRetryingHttpxClient,
    HttpClientConfig,
    HttpClientError,
    RetryingHttpxClient,
    _backoff_delay,
    _parse_retry_after,
    user_agent,
)
from mcpanvil.version import get_version


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
    monkeypatch.setattr("mcpanvil.http.time.sleep", lambda s: delays.append(s))
    return delays


@pytest.fixture
def no_async_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def _fake(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("mcpanvil.http.asyncio.sleep", _fake)
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

    def test_parse_retry_after_fractional_seconds(self) -> None:
        # Non-spec but seen in the wild; honored rather than dropped.
        assert _parse_retry_after("1.5") == 1.5

    def test_parse_retry_after_http_date(self) -> None:
        # A date far in the past yields 0 (never negative).
        assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0

    def test_parse_retry_after_garbage_is_none(self) -> None:
        assert _parse_retry_after("not-a-date") is None
        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None

    def test_parse_retry_after_non_ascii_digit_is_none(self) -> None:
        # Regression: "²" (U+00B2) is str.isdigit() True but float() rejects it.
        # The delta-seconds branch must not raise ValueError out of the retry path.
        assert _parse_retry_after("²") is None
        assert _parse_retry_after("\u0660") is None  # Arabic-Indic zero

    def test_parse_retry_after_non_finite_is_none(self) -> None:
        # "inf"/"nan" parse via float() but must never reach time.sleep().
        assert _parse_retry_after("inf") is None
        assert _parse_retry_after("nan") is None

    def test_jitter_disabled_by_default_is_deterministic(self) -> None:
        cfg = HttpClientConfig(backoff_base=0.5, backoff_max=30.0)
        assert cfg.jitter is False
        assert _backoff_delay(0, cfg, None) == 0.5
        assert _backoff_delay(3, cfg, None) == 4.0

    def test_equal_jitter_keeps_delay_within_bounds(self) -> None:
        cfg = HttpClientConfig(backoff_base=1.0, backoff_max=100.0, jitter=True)
        for attempt in range(5):
            base = min(cfg.backoff_max, cfg.backoff_base * 2.0**attempt)
            for _ in range(64):
                delay = _backoff_delay(attempt, cfg, None)
                assert base / 2.0 <= delay <= base

    def test_jitter_does_not_apply_to_retry_after(self) -> None:
        # The server told us exactly when to retry; don't randomize that.
        cfg = HttpClientConfig(jitter=True, backoff_max=30.0)
        assert _backoff_delay(0, cfg, 5.0) == 5.0
        assert _backoff_delay(0, cfg, 120.0) == 30.0  # still capped


class TestHttpClientConfigValidation:
    def test_negative_max_retries_raises(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            HttpClientConfig(max_retries=-1)

    def test_negative_backoff_base_raises(self) -> None:
        with pytest.raises(ValueError, match="backoff_base"):
            HttpClientConfig(backoff_base=-0.5)

    def test_negative_backoff_max_raises(self) -> None:
        with pytest.raises(ValueError, match="backoff_max"):
            HttpClientConfig(backoff_max=-1.0)

    def test_zero_values_allowed(self) -> None:
        cfg = HttpClientConfig(max_retries=0, backoff_base=0.0, backoff_max=0.0)
        assert cfg.max_retries == 0
        assert cfg.backoff_base == 0.0
        assert cfg.backoff_max == 0.0


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

    def test_transport_error_url_includes_host(self, no_sleep: list[float]) -> None:
        # Transport errors have no Response; the wrapped error url should still
        # carry the host (base_url + path), matching from_response().
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=req)

        transport, _ = _record_transport(handler)
        cfg = HttpClientConfig(max_retries=0)
        with RetryingHttpxClient("https://api.test", config=cfg, transport=transport) as c:
            with pytest.raises(HttpClientError) as exc_info:
                c.get_json("/down")
        assert exc_info.value.url == "https://api.test/down"
        assert "https://api.test/down" in exc_info.value.message

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

    @pytest.mark.anyio
    async def test_transport_error_url_includes_host(self) -> None:
        def handler(i: int, req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=req)

        transport, _ = _record_transport(handler)
        cfg = HttpClientConfig(max_retries=0)
        async with AsyncRetryingHttpxClient(
            "https://api.test", config=cfg, transport=transport
        ) as c:
            with pytest.raises(HttpClientError) as exc_info:
                await c.get_json("/down")
        assert exc_info.value.url == "https://api.test/down"


# ---------------------------------------------------------------------------
# Default outbound User-Agent (#121) — survives the Cloudflare WAF UA ban
# ---------------------------------------------------------------------------


def _ua_capture_transport() -> tuple[httpx.MockTransport, dict[str, str | None]]:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler), seen


class TestClientUserAgent:
    def test_default_user_agent_is_standardized(self) -> None:
        transport, seen = _ua_capture_transport()
        with RetryingHttpxClient("https://api.test", transport=transport) as c:
            c.get_json("/x")
        assert seen["ua"] == user_agent() == f"mcpanvil/{get_version('mcpanvil')}"
        # Must not be a default UA banned by the Cloudflare WAF.
        assert "urllib" not in (seen["ua"] or "").lower()
        assert "python-httpx" not in (seen["ua"] or "").lower()

    def test_component_is_labeled(self) -> None:
        transport, seen = _ua_capture_transport()
        with RetryingHttpxClient("https://api.test", component="awx-mcp", transport=transport) as c:
            c.get_json("/x")
        assert seen["ua"] == user_agent("awx-mcp")
        assert (seen["ua"] or "").startswith("awx-mcp ")

    def test_explicit_user_agent_overrides(self) -> None:
        transport, seen = _ua_capture_transport()
        with RetryingHttpxClient(
            "https://api.test", headers={"User-Agent": "custom/9"}, transport=transport
        ) as c:
            c.get_json("/x")
        assert seen["ua"] == "custom/9"

    def test_explicit_user_agent_override_is_case_insensitive(self) -> None:
        transport, seen = _ua_capture_transport()
        with RetryingHttpxClient(
            "https://api.test", headers={"user-agent": "custom/9"}, transport=transport
        ) as c:
            c.get_json("/x")
        assert seen["ua"] == "custom/9"

    @pytest.mark.anyio
    async def test_async_sends_default_user_agent(self) -> None:
        transport, seen = _ua_capture_transport()
        async with AsyncRetryingHttpxClient("https://api.test", transport=transport) as c:
            await c.get_json("/x")
        assert seen["ua"] == user_agent()
