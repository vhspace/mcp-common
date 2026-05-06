"""HTTP client for Weka REST API.

Provides a synchronous HTTP client for Weka API interactions with connection
pooling, automatic token refresh, and structured error handling.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WekaRestClient:
    """Minimal Weka REST client with automatic token management.

    Auth flow: username/password → ``/api/v2/login`` → Bearer token (expires 5 min).
    Tokens are refreshed automatically before expiry.
    """

    host: str
    username: str
    password: str
    org: str | None = None
    api_base_path: str = "/api/v2"
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    http_transport: httpx.BaseTransport | None = field(default=None, repr=False)

    _client: httpx.Client = field(init=False, repr=False)
    _base_url_str: str = field(init=False, repr=False)
    _access_token: str | None = field(default=None, init=False, repr=False)
    _refresh_token: str | None = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0.0, init=False, repr=False)

    # ── lifecycle ───────────────────────────────────────────────

    def __post_init__(self) -> None:
        host = self.host.rstrip("/")
        base = self.api_base_path.strip("/")
        self._base_url_str = f"{host}/{base}"
        self._client = httpx.Client(
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=self.http_transport,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        self._login()

    def close(self) -> None:
        """Close the HTTP client and release connections."""
        self._client.close()

    def __enter__(self) -> WekaRestClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── URL helpers ─────────────────────────────────────────────

    def _url(self, endpoint: str) -> str:
        return f"{self._base_url_str}/{endpoint.strip('/')}/"

    # ── auth ────────────────────────────────────────────────────

    def _parse_auth_tokens(self, data: dict[str, Any]) -> None:
        """Extract access/refresh tokens from a Weka auth response.

        Weka wraps tokens in ``{"data": [...]}``. Falls back to top-level keys.
        """
        bucket = data
        if "data" in data:
            inner = data["data"]
            if isinstance(inner, dict):
                bucket = inner
            elif isinstance(inner, list) and inner:
                bucket = inner[0]

        self._access_token = bucket.get("access_token")
        self._refresh_token = bucket.get("refresh_token")
        expires_in: int = bucket.get("expires_in", 300)
        self._token_expires_at = time.time() + expires_in

    def _login(self) -> None:
        """Authenticate and obtain access + refresh tokens."""
        url = self._url("login")
        payload: dict[str, Any] = {"username": self.username, "password": self.password}
        if self.org is not None:
            payload["org"] = self.org
        r = self._client.post(url, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Weka login failed: {e.response.status_code} {e.response.text}"
            ) from e

        self._parse_auth_tokens(r.json())
        if not self._access_token:
            raise RuntimeError("Failed to obtain access token from login response")
        logger.debug("Authenticated with Weka cluster at %s", self.host)

    def _refresh_access_token(self) -> None:
        """Refresh the access token, falling back to full re-login on failure."""
        if not self._refresh_token:
            self._login()
            return

        url = self._url("login/refresh")
        r = self._client.post(url, json={"refresh_token": self._refresh_token})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            logger.debug("Token refresh failed, falling back to re-login")
            self._login()
            return

        self._parse_auth_tokens(r.json())
        logger.debug("Access token refreshed")

    def _ensure_valid_token(self) -> None:
        """Refresh if the token expires within 30 seconds."""
        if time.time() >= (self._token_expires_at - 30):
            self._refresh_access_token()

    def _auth_headers(self) -> dict[str, str]:
        self._ensure_valid_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── HTTP verbs ──────────────────────────────────────────────

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
        """Execute an authenticated request and raise on HTTP errors."""
        url = self._url(endpoint)
        headers = self._auth_headers()
        r = self._client.request(method, url, headers=headers, **kwargs)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Weka {method} {url} failed: {e.response.status_code} {e.response.text}"
            ) from e
        return r

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET an endpoint and return parsed JSON."""
        return self._request("GET", endpoint, params=params).json()

    def post(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        """POST to an endpoint and return parsed JSON."""
        return self._request("POST", endpoint, json=json).json()

    def put(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        """PUT to an endpoint and return parsed JSON."""
        return self._request("PUT", endpoint, json=json).json()

    def patch(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        """PATCH an endpoint and return parsed JSON."""
        return self._request("PATCH", endpoint, json=json).json()

    def delete(self, endpoint: str) -> Any:
        """DELETE an endpoint. Returns JSON when available, else a status dict."""
        r = self._request("DELETE", endpoint)
        try:
            return r.json()
        except Exception:
            return {"status": "deleted"}
