import httpx
import pytest

from awx_mcp.awx_client import AwxRestClient, is_stdout_too_large_notice

CAP_NOTICE = (
    "Standard Output too large to display (3214886 bytes), only download "
    "supported for sizes over 1048576 bytes. Note: You can use the API to "
    "fetch the full standard output."
)


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


# ---------------------------------------------------------------------------
# Issue #54 / #44 — cap-notice detection + cap-bypassing stdout download
# ---------------------------------------------------------------------------


STDOUT_PHRASE = "Standard Output too large to display"


class TestIsStdoutTooLargeNotice:
    def test_detects_real_notice(self) -> None:
        assert is_stdout_too_large_notice(CAP_NOTICE) is True

    def test_detects_with_leading_whitespace(self) -> None:
        assert is_stdout_too_large_notice("\n  " + CAP_NOTICE) is True

    def test_empty_is_false(self) -> None:
        assert is_stdout_too_large_notice("") is False

    def test_normal_log_is_false(self) -> None:
        assert is_stdout_too_large_notice("PLAY [all] ***\nok: [host1]\n") is False

    def test_large_log_mentioning_phrase_is_false(self) -> None:
        # A multi-MB log that merely contains the phrase mid-stream is not the gate.
        big = ("x" * 10000) + STDOUT_PHRASE + ("y" * 10000)
        assert is_stdout_too_large_notice(big) is False


def test_get_job_stdout_returns_plain_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("format") == "txt"
        return httpx.Response(200, text="PLAY [all]\nok: [h1]\n")

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.get_job_stdout(123, fmt="txt")
        assert out.content == "PLAY [all]\nok: [h1]\n"
        assert out.capped is False
        assert out.downloaded is False
    finally:
        c.close()


def test_get_job_stdout_bypasses_cap_via_download() -> None:
    seen_formats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fmt = request.url.params.get("format")
        seen_formats.append(fmt or "")
        # text/plain must be negotiated even for the download renderer (avoids 406).
        assert request.headers.get("accept") == "text/plain"
        if fmt == "txt":
            return httpx.Response(200, text=CAP_NOTICE)
        if fmt == "txt_download":
            return httpx.Response(200, text="FULL LOG CONTENT\nPLAY RECAP\n")
        return httpx.Response(406, text="Not Acceptable")

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.get_job_stdout(123, fmt="txt")
        assert out.content == "FULL LOG CONTENT\nPLAY RECAP\n"
        assert out.capped is False
        assert out.downloaded is True
        assert seen_formats == ["txt", "txt_download"]
    finally:
        c.close()


def test_get_job_stdout_download_preserves_line_range() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        fmt = request.url.params.get("format")
        if fmt == "txt":
            return httpx.Response(200, text=CAP_NOTICE)
        if fmt == "txt_download":
            seen["start_line"] = request.url.params.get("start_line")
            seen["end_line"] = request.url.params.get("end_line")
            return httpx.Response(200, text="RANGE")
        return httpx.Response(406, text="Not Acceptable")

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.get_job_stdout(123, fmt="txt", start_line=10, end_line=20)
        assert out.content == "RANGE"
        assert seen == {"start_line": "10", "end_line": "20"}
    finally:
        c.close()


def test_get_job_stdout_no_bypass_reports_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("format") == "txt"
        return httpx.Response(200, text=CAP_NOTICE)

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.get_job_stdout(123, fmt="txt", bypass_cap=False)
        assert out.capped is True
        assert out.downloaded is False
    finally:
        c.close()


def test_get_job_stdout_txt_download_uses_text_plain_accept() -> None:
    """Requesting txt_download directly must negotiate text/plain (the 406 fix)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("format") == "txt_download"
        if request.headers.get("accept") != "text/plain":
            return httpx.Response(406, text="Not Acceptable")
        return httpx.Response(200, text="DOWNLOADED FULL LOG")

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.get_job_stdout(123, fmt="txt_download")
        assert out.content == "DOWNLOADED FULL LOG"
        assert out.capped is False
        assert out.downloaded is True
    finally:
        c.close()


def test_paginate_walks_all_pages_without_mutating_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(200, json={"count": 3, "next": "?page=2", "results": [{"id": 1}]})
        if page == 2:
            return httpx.Response(200, json={"count": 3, "next": "?page=3", "results": [{"id": 2}]})
        return httpx.Response(200, json={"count": 3, "next": None, "results": [{"id": 3}]})

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        params = {"page_size": 200}
        out = c.paginate("jobs/1/job_events", params)
        assert [r["id"] for r in out] == [1, 2, 3]
        # Caller's params dict must be untouched.
        assert params == {"page_size": 200}
    finally:
        c.close()


def test_paginate_respects_max_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(
            200, json={"count": 100, "next": "?page=next", "results": [{"id": page}]}
        )

    c = AwxRestClient(
        host="https://awx.example.com", token="t", http_transport=httpx.MockTransport(handler)
    )
    try:
        out = c.paginate("jobs/1/job_events", {}, max_results=2)
        assert len(out) == 2
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
