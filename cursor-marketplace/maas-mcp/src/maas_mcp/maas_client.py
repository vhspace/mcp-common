"""HTTP client for Canonical MAAS REST API.

This module provides a dedicated HTTP client for MAAS API interactions with
OAuth 1.0 authentication, connection pooling, timeout handling, and error management.

Key Features:
- OAuth 1.0 PLAINTEXT signature authentication
- Connection pooling for efficient multiple requests
- Automatic JSON response parsing
- Configurable timeouts and SSL verification
- Version detection and API compatibility
- Support for multiple MAAS instances

Usage:
    client = MaasRestClient(
        url="https://maas.example.com/MAAS",
        api_key="consumer_key:consumer_token:secret",
        verify_ssl=True
    )

    # Get version
    version_info = client.get_version()

    # List machines
    machines = client.get("machines")

    # Get machine details
    machine = client.get(f"machines/{system_id}")

    client.close()  # Important: close to release connections
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from oauthlib.oauth1 import SIGNATURE_PLAINTEXT  # type: ignore[import-untyped]
from requests_oauthlib import OAuth1Session  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


def is_maas_http_error(exc: BaseException, status_code: int = 404) -> bool:
    """Return True if *exc* is :class:`RuntimeError` from HTTP errors in this client.

    :meth:`MaasRestClient._request` wraps failures as
    ``MAAS <METHOD> <url> failed: <status> <body>``.
    """
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc)
    if not msg.startswith("MAAS "):
        return False
    return f" failed: {status_code} " in msg


@dataclass(slots=True)
class MaasRestClient:
    """
    MAAS REST API client with OAuth 1.0 authentication.

    - Auth: OAuth 1.0 PLAINTEXT via requests_oauthlib
    - Base path: /MAAS/api/2.0/
    - Version detection: Automatic via /api/2.0/version/
    """

    url: str
    api_key: str
    verify_ssl: bool = True
    timeout_seconds: float = 30.0

    _session: OAuth1Session = field(init=False, repr=False)
    _base_url: str = field(init=False, repr=False)
    _version: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        """Initialize OAuth session and detect MAAS version."""
        try:
            consumer_key, consumer_token, secret = self.api_key.split(":", 2)
        except ValueError as e:
            raise ValueError(
                "MAAS API key must be in format: consumer_key:consumer_token:secret"
            ) from e

        self._base_url = self.url.rstrip("/")
        if not self._base_url.endswith("/MAAS"):
            self._base_url = f"{self._base_url}/MAAS"

        self._session = OAuth1Session(
            consumer_key,
            resource_owner_key=consumer_token,
            resource_owner_secret=secret,
            signature_method=SIGNATURE_PLAINTEXT,
        )

        self._version = None

    @property
    def base_url(self) -> str:
        """Public accessor for the base URL."""
        return self._base_url

    def _get_api_url(self, endpoint: str) -> str:
        """Build full API URL for an endpoint."""
        # MAAS API endpoints are canonical with a trailing slash. Some MAAS
        # deployments reject non-canonical paths (404 "Unknown API endpoint"),
        # so we normalize here.
        endpoint = endpoint.strip("/")
        return f"{self._base_url}/api/2.0/{endpoint}/"

    def get_version(self) -> str:
        """
        Get MAAS API version (cached after first call).

        Returns:
            Version string (e.g., "2.0.0")
        """
        if self._version is None:
            try:
                resp = self.get("version")
                self._version = resp.get("version", "2.0.0")
                logger.info("Detected MAAS version: %s", self._version)
            except Exception as e:
                logger.warning("Failed to detect MAAS version: %s, assuming 2.0", e)
                self._version = "2.0.0"
        return self._version

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout_override: float | None = None,
    ) -> requests.Response:
        """Send an HTTP request to the MAAS API with timeout and error handling."""
        url = self._get_api_url(endpoint)
        timeout = timeout_override if timeout_override is not None else self.timeout_seconds
        try:
            resp: requests.Response = self._session.request(
                method,
                url,
                params=params,
                data=data,
                verify=self.verify_ssl,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            body = e.response.text[:500]
            logger.error("MAAS %s %s failed: %s %s", method, url, status, body)
            raise RuntimeError(f"MAAS {method} {url} failed: {status} {body}") from e
        except requests.exceptions.RequestException as e:
            logger.error("MAAS %s %s failed: %s", method, url, e)
            raise RuntimeError(f"MAAS {method} {url} failed: {e}") from e

    @staticmethod
    def _parse_json(resp: requests.Response) -> Any:
        """Return JSON if present; otherwise return text/None."""
        if not resp.text:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET request to MAAS API. Returns parsed JSON."""
        return self._request("GET", endpoint, params=params).json()

    def get_safe(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET request with graceful non-JSON fallback (e.g. BSON details)."""
        resp = self._request("GET", endpoint, params=params)
        return self._parse_json(resp)

    def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """POST request to MAAS API. Returns parsed JSON or text."""
        return self._parse_json(self._request("POST", endpoint, params=params, data=data))

    def post_fire(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> tuple[Any | None, bool]:
        """Fire-and-forget POST: returns (result, timed_out).

        Uses a short timeout. If the server doesn't respond in time,
        returns (None, True) instead of raising — the operation was
        likely accepted by MAAS.
        """
        try:
            resp = self._request(
                "POST",
                endpoint,
                params=params,
                data=data,
                timeout_override=timeout,
            )
            return self._parse_json(resp), False
        except RuntimeError as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                return None, True
            raise

    def put(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """PUT request to MAAS API. Returns parsed JSON or text."""
        return self._parse_json(self._request("PUT", endpoint, params=params, data=data))

    def delete(self, endpoint: str) -> None:
        """DELETE request to MAAS API."""
        self._request("DELETE", endpoint)

    def close(self) -> None:
        """Close the session and release resources."""
        if self._session:
            self._session.close()
