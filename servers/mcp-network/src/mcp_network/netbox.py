"""NetBox helpers — shell out to ``netbox-cli``.

This MCP does not bring a NetBox client of its own; the workspace ships a
``netbox-cli`` binary that already handles auth and caching. We only need
node -> NIC MACs for ``find_port_for_node``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

logger = logging.getLogger(__name__)


NETBOX_CLI = "netbox-cli"


class NetboxUnavailableError(RuntimeError):
    """Raised when ``netbox-cli`` isn't on PATH."""


# Backwards-compatible alias (public API).
NetboxUnavailable = NetboxUnavailableError


async def get_node_nic_macs(node_name: str) -> list[dict[str, Any]]:
    """Return per-NIC info for ``node_name``.

    Output shape::

        [{"name": "enp48s0np0", "mac": "9C:63:C0:26:EF:EC", "type": "..."}, ...]

    Entries without a MAC are dropped. Raises ``NetboxUnavailable`` if
    ``netbox-cli`` isn't installed, or ``RuntimeError`` on lookup failure.
    """
    if shutil.which(NETBOX_CLI) is None:
        raise NetboxUnavailableError(f"{NETBOX_CLI} not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        NETBOX_CLI,
        "list",
        "dcim.interface",
        "--filter",
        f"device={node_name}",
        "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"netbox-cli exited {proc.returncode}: {stderr_b.decode().strip()}")

    try:
        data = json.loads(stdout_b.decode() or "null")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse netbox-cli JSON: {e}") from e

    results = _extract_results(data)
    out: list[dict[str, Any]] = []
    for iface in results:
        mac = iface.get("mac_address")
        if not mac:
            continue
        out.append(
            {
                "name": iface.get("name"),
                "mac": str(mac).lower(),
                "type": (iface.get("type") or {}).get("label")
                if isinstance(iface.get("type"), dict)
                else iface.get("type"),
            }
        )
    return out


def _extract_results(data: Any) -> list[dict[str, Any]]:
    """netbox-cli may return ``{"results": [...]}`` or a bare list."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return [x for x in data["results"] if isinstance(x, dict)]
    return []
