import httpx
import pytest

from awx_mcp.awx_client import AwxRestClient


def test_get_builds_expected_url_and_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(
        host="https://awx.example.com/",
        token="t0k3n",
        api_base_path="/api/v2",
        http_transport=transport,
    )
    try:
        resp = c.get("ping")
        assert resp == {"ok": True}
        assert seen["method"] == "GET"
        assert seen["url"] == "https://awx.example.com/api/v2/ping/"
        assert seen["auth"] == "Bearer t0k3n"
    finally:
        c.close()


def test_get_text_returns_plain_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("accept") == "text/plain"
        return httpx.Response(200, text="hello\nworld\n")

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        out = c.get_text("jobs/123/stdout", params={"format": "txt"})
        assert out == "hello\nworld\n"
    finally:
        c.close()


def test_http_errors_raise_runtime_error_with_status_and_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        with pytest.raises(RuntimeError, match="404"):
            c.get("missing")
    finally:
        c.close()


def test_post_handles_empty_response_body() -> None:
    """POST to cancel/launch endpoints that return 202 with no body."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"")

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        result = c.post("jobs/123/cancel")
        assert result["status_code"] == 202
    finally:
        c.close()


def test_post_handles_json_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 999, "status": "pending"})

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        result = c.post("job_templates/1/launch", json={"extra_vars": {}})
        assert result["id"] == 999
    finally:
        c.close()


def test_delete_handles_204_no_content() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        result = c.delete("jobs/123")
        assert result["status_code"] == 204
    finally:
        c.close()


def test_patch_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        return httpx.Response(200, json={"id": 1, "name": "updated"})

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        result = c.patch("credentials/1", json={"name": "updated"})
        assert result["name"] == "updated"
    finally:
        c.close()


def test_put_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        return httpx.Response(200, json={"id": 1, "name": "replaced"})

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(host="https://awx.example.com", token="t", http_transport=transport)
    try:
        result = c.put("projects/1", json={"name": "replaced"})
        assert result["name"] == "replaced"
    finally:
        c.close()


def test_retry_on_503() -> None:
    """Transient 503 should be retried and succeed on the second attempt."""
    attempt_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(
        host="https://awx.example.com",
        token="t",
        http_transport=transport,
        max_retries=2,
    )
    try:
        result = c.get("ping")
        assert result == {"ok": True}
        assert attempt_count == 2
    finally:
        c.close()


def test_retry_exhausted_raises() -> None:
    """Persistent 503 should raise after all retries are exhausted."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(
        host="https://awx.example.com",
        token="t",
        http_transport=transport,
        max_retries=1,
    )
    try:
        with pytest.raises(RuntimeError, match="503"):
            c.get("ping")
    finally:
        c.close()


def test_non_retryable_error_not_retried() -> None:
    """Non-retryable errors (400, 404, etc.) should fail immediately."""
    attempt_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        return httpx.Response(400, text="Bad Request")

    transport = httpx.MockTransport(handler)
    c = AwxRestClient(
        host="https://awx.example.com",
        token="t",
        http_transport=transport,
        max_retries=3,
    )
    try:
        with pytest.raises(RuntimeError, match="400"):
            c.get("bad-endpoint")
        assert attempt_count == 1
    finally:
        c.close()
