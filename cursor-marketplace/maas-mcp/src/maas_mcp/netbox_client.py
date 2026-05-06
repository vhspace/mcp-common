"""Lightweight NetBox API client for MAAS MCP network config sync.

Provides device lookups by hostname / Provider_Machine_ID and IP extraction.
Uses httpx for async-compatible HTTP requests.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class NetboxClient:
    """Thin NetBox REST client backed by httpx."""

    def __init__(self, url: str, token: str, *, timeout: float = 15.0) -> None:
        self._base = url.rstrip("/")
        self._headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        }
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}/api/{path.lstrip('/')}"
        resp = httpx.get(url, headers=self._headers, params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Device lookups
    # ------------------------------------------------------------------

    def lookup_device(self, hostname: str) -> dict[str, Any] | None:
        """Find a device by hostname or Provider_Machine_ID.

        Returns the first matching device dict with an added
        ``primary_ip4_address`` convenience field (bare IP, no CIDR),
        or *None* if no match.
        """
        device = self._lookup_by_provider_machine_id(hostname)
        if device is None:
            device = self._lookup_by_name(hostname)
        if device is not None:
            device["primary_ip4_address"] = extract_ip(device)
        return device

    def lookup_device_for_site(
        self, hostname: str, maas_zone: str | None = None
    ) -> dict[str, Any] | None:
        """Find a device by hostname, preferring results that match the MAAS zone.

        When multiple NetBox devices share the same hostname (e.g. ``gpu037``
        exists at both ORI-TX and 5C-OH1), the device whose ``site.name`` or
        ``site.slug`` overlaps with *maas_zone* wins.  Falls back to the first
        match if no zone information is available.
        """
        candidates = self._lookup_all_by_name(hostname)

        if not candidates:
            device = self._lookup_by_provider_machine_id(hostname)
            if device is not None:
                device["primary_ip4_address"] = extract_ip(device)
            return device

        if len(candidates) == 1:
            dev = candidates[0]
            dev["primary_ip4_address"] = extract_ip(dev)
            if maas_zone:
                site_name = (dev.get("site") or {}).get("name", "")
                site_slug = (dev.get("site") or {}).get("slug", "")
                zone_lower = maas_zone.lower()
                if not (
                    zone_lower in site_name.lower()
                    or zone_lower in site_slug.lower()
                    or site_name.lower() in zone_lower
                    or site_slug.lower() in zone_lower
                ):
                    logger.warning(
                        "NetBox device %s site=%s may not match MAAS zone=%s",
                        dev.get("name"),
                        site_name,
                        maas_zone,
                    )
            return dev

        if maas_zone:
            zone_lower = maas_zone.lower()
            for dev in candidates:
                site_name = (dev.get("site") or {}).get("name", "").lower()
                site_slug = (dev.get("site") or {}).get("slug", "").lower()
                if (
                    zone_lower in site_name
                    or zone_lower in site_slug
                    or site_name in zone_lower
                    or site_slug in zone_lower
                ):
                    dev["primary_ip4_address"] = extract_ip(dev)
                    logger.info(
                        "NetBox: picked %s (site=%s) for MAAS zone=%s out of %d candidates",
                        dev.get("name"),
                        (dev.get("site") or {}).get("name"),
                        maas_zone,
                        len(candidates),
                    )
                    return dev

            logger.warning(
                "NetBox: %d devices named %s but none match MAAS zone=%s; using first",
                len(candidates),
                hostname,
                maas_zone,
            )

        dev = candidates[0]
        dev["primary_ip4_address"] = extract_ip(dev)
        return dev

    def get_device(self, device_id: int) -> dict[str, Any]:
        """Fetch full device details by numeric ID."""
        return self._get(f"dcim/devices/{device_id}/")

    # ------------------------------------------------------------------
    # Internal search helpers
    # ------------------------------------------------------------------

    def _lookup_by_provider_machine_id(self, value: str) -> dict[str, Any] | None:
        data = self._get(
            "dcim/devices/",
            params={"cf_Provider_Machine_ID": value, "limit": 1},
        )
        results = data.get("results", [])
        return results[0] if results else None

    def _lookup_by_name(self, name: str) -> dict[str, Any] | None:
        data = self._get("dcim/devices/", params={"name": name, "limit": 1})
        results = data.get("results", [])
        return results[0] if results else None

    def _lookup_all_by_name(self, name: str) -> list[dict[str, Any]]:
        """Return all devices matching *name* (may span multiple sites)."""
        data = self._get("dcim/devices/", params={"name": name, "limit": 50})
        return data.get("results", [])


# ----------------------------------------------------------------------
# Pure helpers (no network I/O)
# ----------------------------------------------------------------------


def extract_ip(device: dict[str, Any]) -> str | None:
    """Return the bare IPv4 address from a NetBox device, stripping CIDR."""
    pip4 = device.get("primary_ip4")
    if not pip4:
        return None
    addr = pip4.get("address", "") if isinstance(pip4, dict) else str(pip4)
    return addr.split("/")[0] if addr else None
