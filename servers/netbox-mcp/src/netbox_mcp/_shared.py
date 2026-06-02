"""Domain constants and helpers shared by the MCP server and the companion CLI.

Single source of truth for values that previously lived in — and drifted
between — :mod:`netbox_mcp.server` and :mod:`netbox_mcp.cli`. Both modules
import from here so each constant/helper is defined exactly once.

``netbox_mcp.server`` re-exports these names (``VALID_DEVICE_STATUSES``,
``DEFAULT_SEARCH_TYPES``, ``_is_ip_address``, ``_extract_ip_address``) for
backwards compatibility, so existing ``from netbox_mcp.server import ...``
call sites and tests keep working.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# Accepted NetBox device statuses for write/validation paths. Authoritative
# source for the MCP tool's ``DeviceStatusParam`` enum (server.py) and the
# CLI ``update-device`` validation (cli.py).
VALID_DEVICE_STATUSES = frozenset(
    {"active", "planned", "staged", "failed", "inventory", "decommissioning", "offline"}
)

# Object types searched by ``netbox_search_objects`` when the caller does not
# pass an explicit list. The CLI ``search`` command derives its own default set
# from this list (see ``netbox_mcp.cli._DEFAULT_SEARCH_TYPES``).
DEFAULT_SEARCH_TYPES = [
    "dcim.device",
    "dcim.site",
    "ipam.ipaddress",
    "dcim.interface",
    "dcim.rack",
    "ipam.vlan",
    "circuits.circuit",
    "virtualization.virtualmachine",
]


def _is_ip_address(s: str) -> bool:
    """Return True if *s* is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _extract_ip_address(ip_field: dict[str, Any] | None) -> str | None:
    """Extract bare IP (no CIDR suffix) from a NetBox nested IP object.

    NetBox returns IPs as ``{"id": 1, "address": "10.0.0.1/24", ...}``.
    This helper returns just ``"10.0.0.1"``.
    """
    if ip_field and isinstance(ip_field, dict):
        addr = ip_field.get("address", "")
        return addr.split("/")[0] if addr else None
    return None


def enrich_device_ips(device: dict[str, Any]) -> None:
    """Add bare-IP convenience fields + ``provider_machine_id`` to *device* in place.

    Mirrors NetBox's nested IP objects into CIDR-stripped scalars
    (``primary_ip4_address`` / ``primary_ip6_address`` / ``oob_ip_address``)
    and surfaces the ``Provider_Machine_ID`` custom field as a top-level
    ``provider_machine_id``. Mutates *device*; returns nothing. Consolidates
    the previously duplicated enrichment blocks in ``server.py``.
    """
    device["primary_ip4_address"] = _extract_ip_address(device.get("primary_ip4"))
    device["primary_ip6_address"] = _extract_ip_address(device.get("primary_ip6"))
    device["oob_ip_address"] = _extract_ip_address(device.get("oob_ip"))
    provider_id = device.get("custom_fields", {}).get("Provider_Machine_ID")
    if provider_id:
        device["provider_machine_id"] = provider_id
