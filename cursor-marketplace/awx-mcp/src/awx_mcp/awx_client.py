"""HTTP client for Ansible AWX / Automation Controller REST API."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0


@dataclass(slots=True)
class AwxRestClient:
    """
    Minimal AWX REST client.

    - Auth: OAuth2 PAT via ``Authorization: Bearer <token>``
    - Base path: defaults to ``/api/v2``
    - Retries transient errors (429, 502, 503, 504) with exponential backoff
    """

    host: str
    token: str
    api_base_path: str = "/api/v2"
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    max_retries: int = _MAX_RETRIES
    http_transport: httpx.BaseTransport | None = field(default=None, repr=False)

    _client: httpx.Client = field(init=False, repr=False)
    _base_url_str: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._base_url_str = self._build_base_url()
        self._client = httpx.Client(
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=self.http_transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )

    def _build_base_url(self) -> str:
        host = self.host.rstrip("/")
        base = self.api_base_path.strip("/")
        return f"{host}/{base}"

    def _url(self, endpoint: str) -> str:
        ep = endpoint.strip("/")
        return f"{self._base_url_str}/{ep}/"

    def _raise_for_status(self, r: httpx.Response, method: str, url: str) -> None:
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"AWX {method} {url} failed: {e.response.status_code} {e.response.text}"
            ) from e

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._client.request(method, url, **kwargs)
                if r.status_code not in _RETRYABLE_STATUS_CODES or attempt == self.max_retries:
                    return r
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", wait))
                logger.warning(
                    "AWX %s %s returned %s, retrying in %.1fs", method, url, r.status_code, wait
                )
                time.sleep(wait)
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt == self.max_retries:
                    break
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.warning("AWX %s %s timed out, retrying in %.1fs", method, url, wait)
                time.sleep(wait)
        raise RuntimeError(
            f"AWX {method} {url} failed after {self.max_retries + 1} attempts"
        ) from last_exc

    @staticmethod
    def _parse_json_or_empty(r: httpx.Response, url: str) -> Any:
        if r.status_code == 204 or not r.content:
            return {"status_code": r.status_code, "url": url}
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "url": url}

    def close(self) -> None:
        self._client.close()

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("GET", url, params=params)
        self._raise_for_status(r, "GET", url)
        return r.json()

    def get_text(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "text/plain",
    ) -> str:
        url = self._url(endpoint)
        r = self._request_with_retry("GET", url, params=params, headers={"Accept": accept})
        self._raise_for_status(r, "GET", url)
        return r.text

    def post(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("POST", url, json=json)
        self._raise_for_status(r, "POST", url)
        return self._parse_json_or_empty(r, url)

    def put(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("PUT", url, json=json)
        self._raise_for_status(r, "PUT", url)
        return self._parse_json_or_empty(r, url)

    def patch(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("PATCH", url, json=json)
        self._raise_for_status(r, "PATCH", url)
        return self._parse_json_or_empty(r, url)

    def delete(self, endpoint: str) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("DELETE", url)
        self._raise_for_status(r, "DELETE", url)
        return self._parse_json_or_empty(r, url)
