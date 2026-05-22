"""NetBox-backed service discovery for multi-site MCP configurations.

Fetches site service endpoint configurations from NetBox config contexts
(named ``site:<slug>``) and exposes them as typed Pydantic models.  Secrets
are never stored in NetBox — the models carry ``*_env`` references that are
resolved at runtime by :mod:`mcp_common.credential_chain`.

Usage::

    discovery = NetBoxServiceDiscovery()
    endpoints = discovery.get_services("ori_tx", "ufm")
    for ep in endpoints:
        print(ep.url, ep.auth_type)
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL = 300  # seconds


class AuthType(StrEnum):
    TOKEN = "token"
    PASSWORD = "password"
    API_KEY = "api_key"
    SSH = "ssh"
    NONE = "none"


class ServiceEndpoint(BaseModel):
    """A single service instance at a site."""

    name: str = "default"
    url: str
    api_base_path: str | None = None
    auth_type: AuthType = AuthType.NONE
    token_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    api_key_env: str | None = None
    verify_ssl: bool = True
    timeout_seconds: int = 30
    extra: dict[str, Any] = {}


class SiteServices(BaseModel):
    """All services available at a site, parsed from a config context."""

    ufm: list[ServiceEndpoint] = []
    weka: list[ServiceEndpoint] = []
    maas: list[ServiceEndpoint] = []
    vast: list[ServiceEndpoint] = []
    topaz: dict[str, Any] | None = None


class _CacheEntry:
    """Internal TTL cache wrapper."""

    __slots__ = ("data", "fetched_at")

    def __init__(self, data: dict[str, SiteServices], fetched_at: float) -> None:
        self.data = data
        self.fetched_at = fetched_at

    def expired(self, ttl: float) -> bool:
        return (time.monotonic() - self.fetched_at) >= ttl


class NetBoxServiceDiscovery:
    """Discovers service endpoints from NetBox config contexts.

    Looks for config contexts whose names start with ``site:`` and contain a
    ``site_services`` key in their data payload.  Results are cached in-memory
    with a configurable TTL.

    Parameters
    ----------
    netbox_url
        Base URL of the NetBox instance.  Falls back to the ``NETBOX_URL``
        environment variable.
    netbox_token
        API token for NetBox.  Falls back to ``NETBOX_TOKEN``.
    cache_ttl
        Seconds to cache the full discovery result.  Default 300.
    verify_ssl
        Whether to verify TLS certificates on the NetBox connection.
    """

    def __init__(
        self,
        *,
        netbox_url: str | None = None,
        netbox_token: str | None = None,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        verify_ssl: bool = True,
    ) -> None:
        self._netbox_url = (netbox_url or os.environ.get("NETBOX_URL", "")).rstrip("/")
        self._netbox_token = netbox_token or os.environ.get("NETBOX_TOKEN", "")
        self._cache_ttl = cache_ttl
        self._verify_ssl = verify_ssl
        self._cache: _CacheEntry | None = None

    def _fetch_config_contexts(self) -> list[dict[str, Any]]:
        """Fetch ``site:*`` config contexts from NetBox."""
        if not self._netbox_url or not self._netbox_token:
            logger.warning(
                "NetBox URL or token not configured; skipping service discovery"
            )
            return []

        url = f"{self._netbox_url}/api/extras/config-contexts/?name__isw=site:&limit=100"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {self._netbox_token}",
                "Accept": "application/json",
            },
        )

        ctx: ssl.SSLContext | None = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning("NetBox service discovery failed: %s", exc)
            return []

        results: list[dict[str, Any]] = body.get("results", [])
        return results

    def _parse_contexts(self, contexts: list[dict[str, Any]]) -> dict[str, SiteServices]:
        """Parse raw config context API responses into SiteServices keyed by site slug."""
        sites: dict[str, SiteServices] = {}
        for ctx in contexts:
            name: str = ctx.get("name", "")
            if not name.startswith("site:"):
                continue
            slug = name.removeprefix("site:").strip().lower().replace("-", "_")
            if not slug:
                continue

            data = ctx.get("data", {})
            if not isinstance(data, dict):
                continue

            services_data = data.get("site_services")
            if not services_data or not isinstance(services_data, dict):
                continue

            try:
                site_services = SiteServices.model_validate(services_data)
            except Exception:
                logger.warning("Invalid site_services data for config context %r", name)
                continue

            sites[slug] = site_services
        return sites

    def _load(self) -> dict[str, SiteServices]:
        """Return cached data or fetch fresh from NetBox."""
        if self._cache is not None and not self._cache.expired(self._cache_ttl):
            return self._cache.data

        contexts = self._fetch_config_contexts()
        parsed = self._parse_contexts(contexts)
        self._cache = _CacheEntry(parsed, time.monotonic())
        return parsed

    def get_services(self, site_slug: str, service_type: str) -> list[ServiceEndpoint]:
        """Return service endpoints for a given site and service type.

        Returns an empty list if the site or service type is not found or if
        NetBox is unreachable.
        """
        all_sites = self._load()
        slug = site_slug.strip().lower().replace("-", "_")
        site = all_sites.get(slug)
        if site is None:
            return []

        svc = service_type.strip().lower()
        if svc == "topaz":
            return []

        endpoints: list[ServiceEndpoint] = getattr(site, svc, [])
        if not isinstance(endpoints, list):
            return []
        return list(endpoints)

    def get_sites_with_service(self, service_type: str) -> list[str]:
        """Return site slugs that have at least one endpoint for *service_type*."""
        all_sites = self._load()
        svc = service_type.strip().lower()
        result: list[str] = []
        for slug, site in all_sites.items():
            endpoints = getattr(site, svc, [])
            if isinstance(endpoints, list) and endpoints:
                result.append(slug)
        return sorted(result)

    def invalidate_cache(self) -> None:
        """Force a fresh fetch on the next access."""
        self._cache = None
