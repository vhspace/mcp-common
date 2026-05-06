from __future__ import annotations

from ufm_mcp.ufm_client import UfmRestClient


def test_get_json_sends_auth_header(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://ufm.example.com/ufmRest/app/ufm_version",
        json={"version": "6.15.0"},
    )
    client = UfmRestClient(
        base_url="https://ufm.example.com/",
        token="abc123",
        verify_ssl=False,
        timeout_seconds=30,
    )

    try:
        payload = client.get_json("/ufmRest/app/ufm_version")
        assert payload["version"] == "6.15.0"
        req = httpx_mock.get_requests()[0]
        assert req.headers["Authorization"] == "Basic abc123"
    finally:
        client.close()


def test_get_text_uses_custom_accept_header(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://ufm.example.com/ufm_web/file.txt",
        text="ok",
    )
    client = UfmRestClient(
        base_url="https://ufm.example.com/",
        token=None,
        verify_ssl=True,
        timeout_seconds=10,
    )

    try:
        text = client.get_text("/ufm_web/file.txt", accept="text/plain")
        assert text == "ok"
        req = httpx_mock.get_requests()[0]
        assert req.headers["Accept"] == "text/plain"
    finally:
        client.close()


def test_post_json_tolerates_empty_body(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://ufm.example.com/api/action",
        status_code=200,
        content=b"",
    )
    client = UfmRestClient(
        base_url="https://ufm.example.com/",
        token=None,
        verify_ssl=True,
        timeout_seconds=10,
    )

    try:
        payload = client.post_json("/api/action", json_body={"x": 1})
        assert payload == {"ok": True}
    finally:
        client.close()


def test_post_json_falls_back_to_raw_text(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://ufm.example.com/api/action",
        status_code=200,
        text="plain response",
        headers={"Content-Type": "text/plain"},
    )
    client = UfmRestClient(
        base_url="https://ufm.example.com/",
        token=None,
        verify_ssl=True,
        timeout_seconds=10,
    )

    try:
        payload = client.post_json("/api/action", json_body={"x": 1})
        assert payload == {"ok": True, "raw": "plain response"}
    finally:
        client.close()
