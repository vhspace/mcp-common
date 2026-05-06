"""Bond audit: compare live bond0 active slave (SSH) vs MAAS bond configuration.

Provides both sync helpers (for CLI / ThreadPoolExecutor) and async helpers
(for the MCP server / asyncio.gather).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from maas_mcp.maas_client import MaasRestClient
from maas_mcp.netbox_client import NetboxClient

logger = logging.getLogger(__name__)

SSH_TIMEOUT = 5
SSH_OPTIONS = [
    "-o",
    "ConnectTimeout=5",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "BatchMode=yes",
]
SSH_COMMAND = (
    "cat /sys/class/net/{bond}/bonding/active_slave 2>/dev/null; "
    "echo '|||'; "
    "cat /sys/class/net/{bond}/bonding/slaves 2>/dev/null"
)

SEPARATOR = "|||"


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------


def _parse_ssh_output(raw: str) -> dict[str, Any]:
    """Parse the combined active_slave + slaves output split by ``|||``."""
    parts = raw.split(SEPARATOR, maxsplit=1)
    active = parts[0].strip() if len(parts) > 0 else ""
    slaves_raw = parts[1].strip() if len(parts) > 1 else ""
    slaves = slaves_raw.split() if slaves_raw else []
    return {
        "active_slave": active or None,
        "slaves": slaves,
    }


def ssh_bond_info(hostname: str, bond: str = "bond0") -> dict[str, Any]:
    """Retrieve bond info from a remote host via SSH (synchronous)."""
    cmd = ["ssh", *SSH_OPTIONS, hostname, SSH_COMMAND.format(bond=bond)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT + 5,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {
                "active_slave": None,
                "slaves": [],
                "error": f"ssh exit {result.returncode}: {stderr}",
            }
        return _parse_ssh_output(result.stdout)
    except subprocess.TimeoutExpired:
        return {"active_slave": None, "slaves": [], "error": "ssh timeout"}
    except Exception as exc:
        return {"active_slave": None, "slaves": [], "error": str(exc)}


async def async_ssh_bond_info(hostname: str, bond: str = "bond0") -> dict[str, Any]:
    """Retrieve bond info from a remote host via SSH (async)."""
    cmd_str = SSH_COMMAND.format(bond=bond)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            *SSH_OPTIONS,
            hostname,
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SSH_TIMEOUT + 5)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return {
                "active_slave": None,
                "slaves": [],
                "error": f"ssh exit {proc.returncode}: {err}",
            }
        return _parse_ssh_output(stdout.decode(errors="replace"))
    except TimeoutError:
        return {"active_slave": None, "slaves": [], "error": "ssh timeout"}
    except Exception as exc:
        return {"active_slave": None, "slaves": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# MAAS bond config extraction
# ---------------------------------------------------------------------------


def extract_maas_bond_config(
    machine: dict[str, Any], bond_name: str = "bond0"
) -> dict[str, Any] | None:
    """Extract bond configuration from a MAAS machine detail dict.

    Returns a dict with ``parents``, ``bond_primary``, and ``effective_primary``
    or *None* if the bond interface is not found.
    """
    for iface in machine.get("interface_set", []):
        if iface.get("type") == "bond" and iface.get("name") == bond_name:
            parents_objs = iface.get("parents", [])
            parent_names: list[str] = []
            has_int_ids = False
            for p in parents_objs:
                if isinstance(p, str):
                    parent_names.append(p)
                elif isinstance(p, dict):
                    parent_names.append(p.get("name", str(p.get("id", ""))))
                elif isinstance(p, int):
                    has_int_ids = True
                    break
                else:
                    parent_names.append(str(p))

            if has_int_ids:
                parent_names = _resolve_parent_names(machine, iface)

            params = iface.get("params") or {}
            bond_primary = params.get("bond_primary") or params.get("primary") or None

            effective = (
                bond_primary if bond_primary else (parent_names[0] if parent_names else None)
            )

            return {
                "parents": parent_names,
                "bond_primary": bond_primary,
                "effective_primary": effective,
            }
    return None


def _resolve_parent_names(machine: dict[str, Any], bond_iface: dict[str, Any]) -> list[str]:
    """Resolve parent interface IDs to names from the machine's interface_set."""
    parent_ids = bond_iface.get("parents", [])
    if not parent_ids or not isinstance(parent_ids[0], int):
        return []
    iface_by_id = {
        i["id"]: i["name"] for i in machine.get("interface_set", []) if "id" in i and "name" in i
    }
    return [iface_by_id.get(pid, str(pid)) for pid in parent_ids]


# ---------------------------------------------------------------------------
# NetBox cluster resolution
# ---------------------------------------------------------------------------


def resolve_cluster_hostnames(nb: NetboxClient, cluster: str) -> list[dict[str, str]]:
    """Resolve a NetBox cluster name to a list of active device MAAS hostnames.

    Returns list of dicts with ``netbox_name`` and ``maas_hostname``.
    """
    devices = _list_cluster_devices(nb, cluster)
    results: list[dict[str, str]] = []
    for dev in devices:
        name = dev.get("name", "")
        cf = dev.get("custom_fields") or {}
        maas_hostname = cf.get("Provider_Machine_ID", "")
        if maas_hostname:
            results.append({"netbox_name": name, "maas_hostname": str(maas_hostname).strip()})
        else:
            logger.warning("Device %s has no Provider_Machine_ID, skipping", name)
    return results


def _list_cluster_devices(nb: NetboxClient, cluster: str) -> list[dict[str, Any]]:
    """List all active devices in a NetBox cluster, handling pagination."""
    all_devices: list[dict[str, Any]] = []
    params: dict[str, Any] = {"cluster": cluster, "status": "active", "limit": 100, "offset": 0}
    while True:
        data = nb._get("dcim/devices/", params=params)
        results = data.get("results", [])
        all_devices.extend(results)
        if not data.get("next"):
            break
        params["offset"] += params["limit"]
    return all_devices


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------


def build_audit_result(
    hostname: str,
    system_id: str | None,
    ssh_info: dict[str, Any],
    maas_bond: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a single node audit result dict."""
    ssh_error = ssh_info.get("error")
    ssh_active = ssh_info.get("active_slave")
    ssh_slaves = ssh_info.get("slaves", [])

    result: dict[str, Any] = {
        "hostname": hostname,
        "system_id": system_id,
        "ssh_active_slave": ssh_active,
        "ssh_slaves": ssh_slaves,
        "maas_parents": maas_bond["parents"] if maas_bond else None,
        "maas_bond_primary": maas_bond["bond_primary"] if maas_bond else None,
        "maas_effective_primary": maas_bond["effective_primary"] if maas_bond else None,
        "match": None,
        "error": ssh_error,
    }

    if ssh_error or maas_bond is None:
        if maas_bond is None and not ssh_error:
            result["error"] = "no bond config in MAAS"
        return result

    result["match"] = ssh_active == maas_bond["effective_primary"]
    return result


def build_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    """Build summary counts from a list of audit results."""
    total = len(results)
    errors = sum(1 for r in results if r.get("error"))
    matches = sum(1 for r in results if r.get("match") is True)
    mismatches = sum(1 for r in results if r.get("match") is False)
    return {"total": total, "matches": matches, "mismatches": mismatches, "errors": errors}


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------


def resolve_maas_hostnames(
    client: MaasRestClient,
    hostnames: list[str],
) -> dict[str, dict[str, Any]]:
    """Resolve a list of MAAS hostnames to system_ids and fetch machine details.

    Returns ``{hostname: {"system_id": ..., "machine": ...}}``
    """
    results: dict[str, dict[str, Any]] = {}
    for hostname in hostnames:
        try:
            machines = _normalize_list(client.get("machines", params={"hostname": hostname}))
            if machines:
                m = machines[0]
                results[hostname] = {"system_id": m.get("system_id"), "machine": m}
            else:
                results[hostname] = {
                    "system_id": None,
                    "machine": None,
                    "error": "not found in MAAS",
                }
        except Exception as exc:
            results[hostname] = {"system_id": None, "machine": None, "error": str(exc)}
    return results


def _normalize_list(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and "results" in response:
        return list(response["results"])
    return [response] if response is not None else []
