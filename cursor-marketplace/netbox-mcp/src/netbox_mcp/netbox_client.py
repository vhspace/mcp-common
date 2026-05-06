"""
NetBox Client Library

Provides a base class for NetBox client implementations and a REST API implementation.
"""

import abc
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CLOUDFLARE_SIGNATURES = frozenset(
    {
        "cloudflare",
        "__cf_chl_",
        "cf-mitigated",
        "challenge-platform",
    }
)


class NetBoxClientBase(abc.ABC):
    """
    Abstract base class for NetBox client implementations.

    Defines the read-only interface that can be implemented either via the
    REST API or directly via the ORM in a NetBox plugin.
    """

    @abc.abstractmethod
    def get(
        self,
        endpoint: str,
        id: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Retrieve one or more objects from NetBox.

        Args:
            endpoint: The API endpoint (e.g., 'dcim/sites', 'ipam/prefixes')
            id: Optional ID to retrieve a specific object
            params: Optional query parameters for filtering

        Returns:
            For single object queries (with id): Returns the object dict
            For list queries (without id): Returns the full paginated response dict with:
                - count: Total number of objects matching the query
                - next: URL to next page (or null if no more pages)
                - previous: URL to previous page (or null if on first page)
                - results: Array of objects for this page
        """
        pass

    @abc.abstractmethod
    def patch(
        self,
        endpoint: str,
        id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Partially update an object in NetBox.

        Args:
            endpoint: The API endpoint (e.g., 'dcim/devices')
            id: The ID of the object to update
            data: Dictionary of fields to update

        Returns:
            The updated object dict
        """
        pass


class NetBoxRestClient(NetBoxClientBase):
    """NetBox client implementation using the REST API."""

    def __init__(self, url: str, token: str, verify_ssl: bool = True):
        """
        Initialize the REST API client.

        Args:
            url: The base URL of the NetBox instance (e.g., 'https://netbox.example.com')
            token: API token for authentication
            verify_ssl: Whether to verify SSL certificates
        """
        self.base_url = url.rstrip("/")
        self.api_url = f"{self.base_url}/api"
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = 30
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _build_url(self, endpoint: str, id: int | None = None) -> str:
        """Build the full URL for an API request."""
        endpoint = endpoint.strip("/")
        if id is not None:
            return f"{self.api_url}/{endpoint}/{id}/"
        return f"{self.api_url}/{endpoint}/"

    def get(
        self,
        endpoint: str,
        id: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Retrieve one or more objects from NetBox via the REST API.

        Args:
            endpoint: The API endpoint (e.g., 'dcim/sites', 'ipam/prefixes')
            id: Optional ID to retrieve a specific object
            params: Optional query parameters for filtering

        Returns:
            For single object queries (with id): Returns the object dict
            For list queries (without id): Returns the full paginated response dict

        Raises:
            requests.HTTPError: If the request fails
        """
        url = self._build_url(endpoint, id)
        logger.debug("GET %s params=%s", url, params)
        response = self.session.get(
            url, params=params, verify=self.verify_ssl, timeout=self.timeout
        )
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.content))
        response.raise_for_status()

        result: dict[str, Any] | list[dict[str, Any]] = response.json()
        return result

    def patch(
        self,
        endpoint: str,
        id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Partially update an object in NetBox via the REST API.

        Args:
            endpoint: The API endpoint (e.g., 'dcim/devices')
            id: The ID of the object to update
            data: Dictionary of fields to update

        Returns:
            The updated object dict

        Raises:
            requests.HTTPError: If the request fails
        """
        url = self._build_url(endpoint, id)
        logger.debug("PATCH %s data=%s", url, data)
        response = self.session.patch(
            url, json=data, verify=self.verify_ssl, timeout=self.timeout
        )
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.content))
        response.raise_for_status()

        result: dict[str, Any] = response.json()
        return result

    def check_vpn(self) -> bool:
        """Probe the API to verify VPN connectivity for write operations.

        Makes a PATCH request to a safe endpoint.  On VPN, NetBox returns a
        normal JSON error (e.g. 405).  Off VPN, Cloudflare intercepts the
        write and returns a 403 HTML block page.

        Returns ``True`` when VPN-connected (writes allowed).
        """
        try:
            url = self._build_url("status")
            response = self.session.patch(url, json={}, verify=self.verify_ssl, timeout=10)
            return not _is_cloudflare_block(response)
        except requests.ConnectionError:
            return False
        except Exception:
            logger.debug("VPN connectivity check failed", exc_info=True)
            return False


def _is_cloudflare_block(response: requests.Response) -> bool:
    """Return ``True`` if *response* is a Cloudflare WAF block page."""
    if response.status_code != 403:
        return False
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        return False
    body_lower = response.text[:2000].lower()
    return any(sig in body_lower for sig in _CLOUDFLARE_SIGNATURES)
