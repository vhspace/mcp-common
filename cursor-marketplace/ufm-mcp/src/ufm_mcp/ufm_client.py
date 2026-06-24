"""Thin HTTP client for NVIDIA UFM REST APIs."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UfmRestClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        verify_ssl: bool,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._timeout = httpx.Timeout(timeout_seconds)

        headers = {
            "Accept": "application/json",
        }
        # Docs show: -H "Authorization:Basic <token>" but most HTTP stacks expect
        # "Authorization: Basic <token>".
        # We send the standard spacing.
        if token:
            headers["Authorization"] = f"Basic {token}"

        self._client = httpx.Client(
            verify=verify_ssl,
            timeout=self._timeout,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path)
        logger.debug("GET %s params=%s", url, params)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_text(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> str:
        url = self._url(path)
        logger.debug("GET (text) %s params=%s", url, params)
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        resp = self._client.get(url, params=params, headers=headers or None)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _parse_json_response(resp: httpx.Response) -> Any:
        """Parse a response that may be empty, JSON, or plain text."""
        if not resp.content:
            return {"ok": True}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"ok": True, "raw": resp.text}

    def post_json(self, path: str, *, json_body: Any) -> Any:
        url = self._url(path)
        logger.debug("POST %s", url)
        resp = self._client.post(url, json=json_body)
        resp.raise_for_status()
        return self._parse_json_response(resp)

    def post_no_body(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """POST with no JSON body (useful for endpoints driven by query params)."""
        url = self._url(path)
        logger.debug("POST (no body) %s params=%s", url, params)
        resp = self._client.post(url, params=params)
        resp.raise_for_status()
        return resp

    def delete_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path)
        logger.debug("DELETE %s params=%s", url, params)
        resp = self._client.delete(url, params=params)
        resp.raise_for_status()
        return self._parse_json_response(resp)
