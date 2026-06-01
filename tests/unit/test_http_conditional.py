"""Tests for the ETag / conditional-GET helpers (#83)."""

from __future__ import annotations

from typing import Any

import httpx

from mcp_common.http import (
    ETagStore,
    apply_conditional_headers,
    conditional_get,
    record_response,
)


class _FakeHeaders:
    """Case-insensitive header mapping mimicking requests/httpx headers."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data = dict(data or {})

    def get(self, key: str, default: str | None = None) -> str | None:
        for k, v in self._data.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = _FakeHeaders(headers)


class _FakeSession:
    """Records each GET's headers; returns queued responses in order."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(dict(kwargs.get("headers") or {}))
        return self._responses.pop(0)


URL = "https://api.test/dcim/sites"
KEY = "dcim.site"


class TestApplyConditionalHeaders:
    def test_no_validator_known_leaves_headers_unchanged(self) -> None:
        store = ETagStore()
        out = apply_conditional_headers(store, KEY, {"Accept": "application/json"})
        assert out == {"Accept": "application/json"}
        assert "If-None-Match" not in out

    def test_if_none_match_added_when_etag_known(self) -> None:
        store = ETagStore()
        store.remember(KEY, etag='"v1"')
        out = apply_conditional_headers(store, KEY, {"Accept": "application/json"})
        assert out["If-None-Match"] == '"v1"'
        assert out["Accept"] == "application/json"

    def test_does_not_mutate_input_headers(self) -> None:
        store = ETagStore()
        store.remember(KEY, etag='"v1"')
        original = {"Accept": "application/json"}
        apply_conditional_headers(store, KEY, original)
        assert "If-None-Match" not in original

    def test_last_modified_fallback_only_when_enabled(self) -> None:
        disabled = ETagStore()
        disabled.remember(KEY, last_modified="Wed, 21 Oct 2015 07:28:00 GMT")
        assert "If-Modified-Since" not in apply_conditional_headers(disabled, KEY)

        enabled = ETagStore(use_last_modified=True)
        enabled.remember(KEY, last_modified="Wed, 21 Oct 2015 07:28:00 GMT")
        out = apply_conditional_headers(enabled, KEY)
        assert out["If-Modified-Since"] == "Wed, 21 Oct 2015 07:28:00 GMT"

    def test_etag_preferred_over_last_modified(self) -> None:
        store = ETagStore(use_last_modified=True)
        store.remember(KEY, etag='"v1"', last_modified="Wed, 21 Oct 2015 07:28:00 GMT")
        out = apply_conditional_headers(store, KEY)
        assert out["If-None-Match"] == '"v1"'
        assert "If-Modified-Since" not in out


class TestRecordResponse:
    def test_captures_etag_on_200(self) -> None:
        store = ETagStore()
        record_response(store, KEY, _FakeResponse(200, {"ETag": '"v1"'}))
        assert store.get_etag(KEY) == '"v1"'

    def test_missing_etag_is_idempotent(self) -> None:
        store = ETagStore()
        store.remember(KEY, etag='"v1"')
        record_response(store, KEY, _FakeResponse(200, {}))  # no ETag header
        assert store.get_etag(KEY) == '"v1"'  # left untouched


class TestConditionalGet:
    def test_first_request_emits_no_if_none_match_and_captures_etag(self) -> None:
        store = ETagStore()
        session = _FakeSession([_FakeResponse(200, {"ETag": '"v1"'})])
        result = conditional_get(session, URL, key=KEY, store=store)

        assert result.status == 200
        assert result.not_modified is False
        assert "If-None-Match" not in session.requests[0]
        assert store.get_etag(KEY) == '"v1"'

    def test_second_request_sends_if_none_match_and_handles_304(self) -> None:
        store = ETagStore()
        session = _FakeSession([_FakeResponse(200, {"ETag": '"v1"'}), _FakeResponse(304)])

        conditional_get(session, URL, key=KEY, store=store)
        result = conditional_get(session, URL, key=KEY, store=store)

        assert session.requests[1]["If-None-Match"] == '"v1"'
        assert result.status == 304
        assert result.not_modified is True
        # 304 must not clobber the cached validator
        assert store.get_etag(KEY) == '"v1"'

    def test_200_updates_stored_etag(self) -> None:
        store = ETagStore()
        session = _FakeSession(
            [_FakeResponse(200, {"ETag": '"v1"'}), _FakeResponse(200, {"ETag": '"v2"'})]
        )
        conditional_get(session, URL, key=KEY, store=store)
        conditional_get(session, URL, key=KEY, store=store)
        assert store.get_etag(KEY) == '"v2"'

    def test_unpacks_as_status_and_response(self) -> None:
        store = ETagStore()
        session = _FakeSession([_FakeResponse(200, {"ETag": '"v1"'})])
        status, response = conditional_get(session, URL, key=KEY, store=store)
        assert status == 200
        assert response.status_code == 200

    def test_works_with_real_httpx_client(self) -> None:
        """Proves the Protocol surface drops in for httpx (not just requests)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, headers={"ETag": '"v1"'}, json={"sites": []})

        store = ETagStore()
        with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://nb.test") as c:
            first = conditional_get(c, "/api/dcim/sites/", key=KEY, store=store)
            second = conditional_get(c, "/api/dcim/sites/", key=KEY, store=store)

        assert first.status == 200
        assert second.not_modified is True
        assert store.get_etag(KEY) == '"v1"'

    def test_last_modified_round_trip(self) -> None:
        store = ETagStore(use_last_modified=True)
        lm = "Wed, 21 Oct 2015 07:28:00 GMT"
        session = _FakeSession([_FakeResponse(200, {"Last-Modified": lm}), _FakeResponse(304)])

        conditional_get(session, URL, key=KEY, store=store)
        result = conditional_get(session, URL, key=KEY, store=store)

        assert session.requests[1]["If-Modified-Since"] == lm
        assert result.not_modified is True

    def test_forget_and_clear(self) -> None:
        store = ETagStore()
        store.remember(KEY, etag='"v1"')
        store.forget(KEY)
        assert store.get_etag(KEY) is None

        store.remember("a", etag='"a"')
        store.remember("b", etag='"b"')
        store.clear()
        assert store.get_etag("a") is None
        assert store.get_etag("b") is None
