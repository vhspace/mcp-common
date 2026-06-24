"""Supermicro OEM persistent (fixed) boot order via Redfish.

Supermicro BMCs expose a persistent UEFI boot order at:
    /redfish/v1/Systems/{id}/Oem/Supermicro/FixedBootOrder

where ``{id}`` is the system member ID (commonly ``1`` but may be
``Self`` or another value depending on BMC firmware).

This is separate from the standard Redfish BootSourceOverride (which is
a one-time or persistent *override*). The FixedBootOrder controls the
default UEFI boot sequence stored in NVRAM.

PATCH requires the ``If-Match`` header with the resource ETag and
typically returns 202 Accepted. A system reset is needed to apply.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .redfish import RedfishClient

logger = logging.getLogger("redfish_mcp.supermicro_boot_order")

_OEM_SUFFIX = "/Oem/Supermicro"
_FBO_SUFFIX = "/Oem/Supermicro/FixedBootOrder"

# Legacy constant kept for backward compatibility; new code should use
# _fixed_boot_order_path() instead.
FIXED_BOOT_ORDER_PATH = "/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"


def _discover_system_id(client: RedfishClient) -> str:
    """Discover the primary system member path from ``/redfish/v1/Systems``.

    Returns the ``@odata.id`` of the first member, e.g.
    ``/redfish/v1/Systems/1`` or ``/redfish/v1/Systems/Self``.
    """
    ep = client.discover_system()
    return ep.system_path


def _oem_supermicro_url(client: RedfishClient) -> str:
    """Return the absolute Supermicro OEM namespace URL for the discovered system."""
    system_path = _discover_system_id(client)
    return f"{client.base_url}{system_path}{_OEM_SUFFIX}"


def _fixed_boot_order_url(client: RedfishClient) -> str:
    """Return the absolute FixedBootOrder URL for the discovered system."""
    system_path = _discover_system_id(client)
    return f"{client.base_url}{system_path}{_FBO_SUFFIX}"


def is_supermicro(client: RedfishClient) -> bool:
    """Check whether the BMC exposes the Supermicro OEM namespace."""
    url = _oem_supermicro_url(client)
    data, err = client.get_json_maybe(url)
    return data is not None and err is None


def get_fixed_boot_order(
    client: RedfishClient,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """GET the current Supermicro FixedBootOrder.

    Returns:
        (data, etag, error) where *data* is the parsed JSON body,
        *etag* is the ETag header (for If-Match on PATCH), and
        *error* is a human-readable error string or None on success.
    """
    url = _fixed_boot_order_url(client)
    try:
        logger.debug("GET %s", url)
        r = client.session.get(url, timeout=client.timeout_s)
        if r.status_code >= 400:
            return None, None, f"{r.status_code} {r.text[:500]}"
        try:
            data = r.json()
        except Exception:
            return None, None, f"non-json response (status {r.status_code})"
        etag = r.headers.get("ETag")
        return data, etag, None
    except Exception as e:
        return None, None, str(e)


def set_fixed_boot_order(
    client: RedfishClient,
    boot_order: dict[str, Any],
) -> dict[str, Any]:
    """PATCH the Supermicro FixedBootOrder.

    Fetches the current resource first to obtain the ETag, then sends the
    PATCH with ``If-Match``.

    Args:
        client: Authenticated RedfishClient.
        boot_order: The payload to PATCH (e.g. ``{"BootModeSelected": "UEFI",
                    "UefiBootOrder#0": {...}, ...}``).

    Returns:
        Result dict with ``ok``, ``http_status``, and details.
    """
    _current, etag, err = get_fixed_boot_order(client)
    if err:
        return {
            "ok": False,
            "error": f"Failed to GET current boot order: {err}",
        }

    url = _fixed_boot_order_url(client)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if isinstance(etag, str) and etag.strip():
        headers["If-Match"] = etag

    logger.info("PATCH %s (If-Match: %s)", url, etag or "<none>")
    resp = client.session.patch(
        url,
        headers=headers,
        data=json.dumps(boot_order),
        timeout=client.timeout_s,
    )

    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"PATCH failed: {resp.status_code}",
            "detail": resp.text[:2000],
            "http_status": resp.status_code,
        }

    return {
        "ok": True,
        "http_status": resp.status_code,
        "etag_sent": etag,
        "note": "System reset required to apply new boot order",
    }
