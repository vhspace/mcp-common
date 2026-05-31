"""MCP Server implementation for Canonical MAAS.

This module defines the FastMCP server and all MCP tools for interacting with MAAS.
It provides a complete interface to MAAS functionality through MCP-compatible tools
that can be used by AI assistants and other MCP clients.

Key Features:
- Multi-instance support (multiple MAAS servers)
- Comprehensive MAAS API coverage through MCP tools
- OAuth 1.0 authentication
- JSON serialization safety for all responses
- Authentication support for HTTP transport
- MCP tool annotations (readOnlyHint, destructiveHint)
- MCP client logging (ctx.info / ctx.warning / ctx.error)
- Structured content with output schemas (ToolResult)
- Elicitation confirmations for destructive write operations
- Argument completions for prompt parameters
- Parameterized resource templates
- MCP resources and prompts
"""

import argparse
import asyncio
import atexit
import ipaddress
import json
import logging
import os
import sys
from typing import Annotated, Any, NoReturn, cast

import mcp.types as mcp_types
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from mcp_common import HttpAccessTokenAuth, add_health_route, create_http_app, suppress_ssl_warnings
from mcp_common.agent_remediation import mcp_remediation_wrapper
from pydantic import Field
from redfish_mcp.hints import HINTS as REDFISH_HINTS

from maas_mcp.config import Settings, configure_logging
from maas_mcp.drift_auditor import compare_bios, compare_nics, compare_storage
from maas_mcp.maas_client import MaasRestClient
from maas_mcp.netbox_client import NetboxClient, extract_ip
from maas_mcp.netbox_helper import extract_network_profile, match_interfaces_by_mac
from maas_mcp.netbox_resolve import (
    NetboxResolveFailureKind,
    NetboxResolveResult,
    format_netbox_resolution_hint,
    resolve_netbox_device_to_maas_system_id,
)
from maas_mcp.node_status import (
    NODE_STATUS_REFERENCE,
    apply_status_coercion_to_machine_params,
)
from maas_mcp.redfish_bmc import (
    RedfishError,
    create_account,
    find_account,
    get_account_detail,
    get_account_service_info,
    list_accounts,
    patch_account,
    set_account_password,
    verify_login,
)

logger = logging.getLogger(__name__)

# Reusable parameter type definitions
InstanceParam = Annotated[str, Field(default="default", description="MAAS instance name")]
FieldsParam = Annotated[list[str] | None, Field(default=None, description="Fields to return")]


def parse_cli_args() -> dict[str, Any]:
    """Parse command-line arguments for configuration overrides."""
    parser = argparse.ArgumentParser(
        description="MAAS MCP Server - Model Context Protocol server for MAAS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--maas-url", type=str, help="Base URL of the MAAS instance")
    parser.add_argument("--maas-api-key", type=str, help="API key (consumer_key:token:secret)")
    parser.add_argument("--maas-instances", type=str, help="JSON dict of named MAAS instances")
    parser.add_argument("--netbox-url", type=str, help="Base URL of NetBox instance")
    parser.add_argument("--netbox-token", type=str, help="NetBox API token")
    parser.add_argument("--netbox-mcp-server", type=str, help="NetBox MCP server name")
    parser.add_argument("--transport", type=str, choices=["stdio", "http"], help="MCP transport")
    parser.add_argument("--host", type=str, help="Host for HTTP server")
    parser.add_argument("--port", type=int, help="Port for HTTP server")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument("--verify-ssl", action="store_true", dest="verify_ssl", default=None)
    ssl_group.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")

    parser.add_argument("--timeout-seconds", type=float, help="HTTP client timeout in seconds")
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity level",
    )

    args = parser.parse_args()
    return {k: v for k, v in vars(args).items() if v is not None}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_json_serializable(obj: Any) -> Any:
    """Ensure an object is JSON-serializable by converting non-serializable types."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ensure_json_serializable(item) for item in obj]
    return str(obj)


def _safe_dict(obj: Any) -> dict[str, Any]:
    return cast(dict[str, Any], _ensure_json_serializable(obj))


def _safe_list(obj: Any) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _ensure_json_serializable(obj))


def _normalize_list_response(response: Any) -> list[Any]:
    """Normalize MAAS API responses that may be a list, a dict with 'results', or a single item."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and "results" in response:
        return list(response["results"])
    return [response] if response is not None else []


def _select_fields(obj: Any, fields: list[str] | None) -> Any:
    """Project dict objects down to a subset of keys to reduce token usage."""
    if not fields:
        return obj
    if isinstance(obj, list):
        return [_select_fields(item, fields) for item in obj]
    if isinstance(obj, dict):
        return {f: obj[f] for f in fields if f in obj}
    return obj


def _resolve_system_id(
    client: MaasRestClient,
    system_id: str | None,
    machine_id: int | None,
) -> str:
    """Resolve a machine identifier to a system_id, fetching from MAAS if needed.

    Falls back to NetBox lookup when the identifier isn't a known MAAS system_id:
    looks up the device in NetBox, extracts custom_fields.Provider_Machine_ID
    (the MAAS hostname), then searches MAAS by that hostname.
    """
    if system_id:
        try:
            client.get(f"machines/{system_id}")
            return system_id
        except RuntimeError as exc:
            from maas_mcp.maas_client import is_maas_http_error

            if not is_maas_http_error(exc, 404):
                raise
            nb_res = _resolve_via_netbox_result(system_id, client)
            if nb_res.system_id:
                return nb_res.system_id
            _raise_machine_lookup_failed(system_id, exc, netbox=nb_res)

    if machine_id is None:
        raise ValueError("Either system_id or machine_id must be provided")

    machines = _normalize_list_response(client.get("machines", params={"id": str(machine_id)}))
    if not machines:
        raise RuntimeError(f"Machine with ID {machine_id} not found")

    resolved = machines[0].get("system_id")
    if not resolved:
        raise RuntimeError(f"Machine with ID {machine_id} has no system_id")

    return str(resolved)


def _raise_machine_lookup_failed(
    identifier: str,
    cause: RuntimeError,
    *,
    netbox: NetboxResolveResult | None = None,
) -> NoReturn:
    """Raise MAAS 404 with NetBox resolution hints for MCP consumers."""
    specific = format_netbox_resolution_hint(netbox) if netbox else ""
    suffix = f" {specific}" if specific else ""
    raise RuntimeError(
        f"No MAAS machine matched {identifier!r}. ({cause}){suffix} "
        "If this is a NetBox device name, set NETBOX_URL and NETBOX_TOKEN so "
        "maas-mcp can resolve it via custom_fields.Provider_Machine_ID -> MAAS hostname. "
        "MAAS system_id and vendor hostname differ from NetBox tenant names."
    ) from cause


def _resolve_via_netbox_result(identifier: str, client: MaasRestClient) -> NetboxResolveResult:
    """Try to resolve a NetBox device name to a MAAS system_id (structured outcome)."""
    try:
        nb = _get_netbox()
    except Exception as exc:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.NOT_CONFIGURED,
            str(exc) or None,
        )

    return resolve_netbox_device_to_maas_system_id(
        identifier,
        client,
        netbox_client=nb,
        on_resolved=lambda i, h, s: logger.info(
            "Resolved NetBox device %r -> MAAS hostname %r (system_id: %s)",
            i,
            h,
            s,
        ),
    )


def _resolve_audit_targets(
    client: MaasRestClient,
    *,
    machine_ids: list[int] | None,
    system_ids: list[str] | None,
    baseline_machine_id: int | None,
    baseline_system_id: str | None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve baseline machine and target system_ids for audit tools.

    Returns (baseline_machine_dict, baseline_system_id, target_system_ids).
    """
    if baseline_system_id:
        sid = baseline_system_id
        try:
            baseline = client.get(f"machines/{sid}")
        except RuntimeError as exc:
            from maas_mcp.maas_client import is_maas_http_error

            if not is_maas_http_error(exc, 404):
                raise
            nb_res = _resolve_via_netbox_result(sid, client)
            if not nb_res.system_id:
                _raise_machine_lookup_failed(sid, exc, netbox=nb_res)
            baseline = client.get(f"machines/{nb_res.system_id}")
            baseline_system_id = nb_res.system_id
    elif baseline_machine_id is not None:
        machines = _normalize_list_response(
            client.get("machines", params={"id": str(baseline_machine_id)})
        )
        if not machines:
            raise RuntimeError(f"Baseline machine {baseline_machine_id} not found")
        baseline = machines[0]
        baseline_system_id = baseline.get("system_id")
    else:
        raise ValueError("Either baseline_machine_id or baseline_system_id must be provided")

    if not baseline_system_id:
        raise RuntimeError("Baseline machine has no system_id")

    target_ids: list[str] = list(system_ids or [])
    for mid in machine_ids or []:
        machines = _normalize_list_response(client.get("machines", params={"id": str(mid)}))
        if machines:
            sid = machines[0].get("system_id")
            if sid:
                target_ids.append(sid)

    if not target_ids:
        raise ValueError(
            f"No targets could be resolved. machine_ids={machine_ids}, system_ids={system_ids}"
        )

    return baseline, baseline_system_id, target_ids


def _set_machine_power_parameters_impl(
    client: MaasRestClient,
    *,
    system_id: str,
    power_address: str | None = None,
    power_user: str | None = None,
    power_pass: str | None = None,
    skip_check: bool = True,
) -> dict[str, Any]:
    """Internal helper to update MAAS machine power parameters."""
    data: dict[str, Any] = {"power_parameters_skip_check": "true" if skip_check else "false"}
    if power_address is not None:
        data["power_parameters_power_address"] = power_address
    if power_user is not None:
        data["power_parameters_power_user"] = power_user
    if power_pass is not None:
        data["power_parameters_power_pass"] = power_pass

    _ = client.put(f"machines/{system_id}", data=data)
    return {
        "ok": True,
        "system_id": system_id,
        "updated_keys": sorted(
            k
            for k in data
            if k not in ("power_parameters_power_pass", "power_parameters_skip_check")
        ),
    }


def _migrate_copy_power_from_source(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Copy power_type and all power parameters from source to target.

    Reads the source machine's power_type and op=power_parameters, then
    applies them to the target via PUT /machines/{sid}/.
    """
    source_machine = source_client.get(f"machines/{source_sid}")
    source_power_type = source_machine.get("power_type") or ""
    raw = source_client.get(f"machines/{source_sid}", params={"op": "power_parameters"})

    data: dict[str, str] = {"power_parameters_skip_check": "true"}
    if source_power_type:
        data["power_type"] = source_power_type

    for param_key in (
        "power_address",
        "power_user",
        "power_driver",
        "power_boot_type",
        "privilege_level",
        "cipher_suite_id",
    ):
        val = raw.get(param_key)
        if val is not None and str(val).strip():
            data[f"power_parameters_{param_key}"] = str(val).strip()

    passwd = raw.get("power_pass")
    if passwd is not None and str(passwd).strip() and str(passwd).strip() != "***":
        data["power_parameters_power_pass"] = str(passwd).strip()

    report_keys = sorted(
        k for k in data if k not in ("power_parameters_power_pass", "power_parameters_skip_check")
    )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_update_keys": report_keys,
            "note": "" if report_keys else "No copyable power fields from source.",
        }

    if not report_keys:
        return {"ok": True, "skipped": True, "reason": "no_copyable_power_fields"}

    target_client.put(f"machines/{target_sid}", data=data)
    return {"ok": True, "updated_keys": report_keys}


# States in which MAAS allows interface create/delete operations.
_IFACE_MUTABLE_STATUSES = ("New", "Ready", "Failed testing", "Allocated", "Broken")

_METADATA_FIELDS = (
    "hostname",
    "architecture",
    "description",
    "osystem",
    "distro_series",
    "hwe_kernel",
)
_METADATA_ADMIN_FIELDS = ("cpu_count", "memory", "zone", "pool")


def _get_boot_interface_mac(machine: dict) -> str | None:
    """Return the lowercase MAC of the machine's boot interface, or None."""
    boot_iface = machine.get("boot_interface")
    if isinstance(boot_iface, dict):
        mac = boot_iface.get("mac_address")
        if mac:
            return mac.lower()
    return None


def _migrate_sync_interfaces(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Replicate physical interfaces (with MACs and names) from source to target.

    Compares by MAC address.  Stale target interfaces whose MAC is absent
    from the source set are deleted; missing interfaces are created via
    ``op=create_physical``.  Already-matching MACs are skipped (idempotent).

    The target machine must be in a state that allows interface mutations
    (New, Ready, Failed testing, Allocated, or Broken).
    """
    target_machine = target_client.get(f"machines/{target_sid}")
    target_status = target_machine.get("status_name") or ""
    if target_status not in _IFACE_MUTABLE_STATUSES:
        return {
            "ok": False,
            "error": (
                f"Target {target_sid} is in '{target_status}'. "
                f"Must be one of {', '.join(_IFACE_MUTABLE_STATUSES)} to modify interfaces. "
                "Release the machine first if it is Deployed."
            ),
        }

    source_machine = source_client.get(f"machines/{source_sid}")
    source_ifaces = [
        {
            "name": iface.get("name") or "",
            "mac_address": (iface.get("mac_address") or "").lower(),
            "effective_mtu": iface.get("effective_mtu"),
        }
        for iface in (source_machine.get("interface_set") or [])
        if iface.get("type") == "physical" and iface.get("mac_address")
    ]
    source_mac_set = {i["mac_address"] for i in source_ifaces}

    target_ifaces = [
        {
            "id": iface.get("id"),
            "name": iface.get("name") or "",
            "mac_address": (iface.get("mac_address") or "").lower(),
            "type": iface.get("type") or "",
        }
        for iface in (target_machine.get("interface_set") or [])
    ]
    target_physical = [i for i in target_ifaces if i["type"] == "physical"]
    target_mac_set = {i["mac_address"] for i in target_physical if i["mac_address"]}

    to_delete = [
        i for i in target_physical if i["mac_address"] and i["mac_address"] not in source_mac_set
    ]
    to_skip = [i for i in target_physical if i["mac_address"] in source_mac_set]
    to_create = [i for i in source_ifaces if i["mac_address"] not in target_mac_set]

    # Build rename plan: skipped interfaces whose name differs from source
    source_name_by_mac = {i["mac_address"]: i["name"] for i in source_ifaces}
    to_rename = [
        {
            "id": i["id"],
            "old_name": i["name"],
            "new_name": source_name_by_mac[i["mac_address"]],
            "mac": i["mac_address"],
        }
        for i in to_skip
        if source_name_by_mac.get(i["mac_address"])
        and i["name"] != source_name_by_mac[i["mac_address"]]
    ]

    source_boot_mac = _get_boot_interface_mac(source_machine)

    if dry_run:
        dry_result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "would_delete": [
                {"id": i["id"], "name": i["name"], "mac": i["mac_address"]} for i in to_delete
            ],
            "would_create": [{"name": i["name"], "mac": i["mac_address"]} for i in to_create],
            "would_rename": to_rename,
            "skipped": [{"name": i["name"], "mac": i["mac_address"]} for i in to_skip],
        }
        if source_boot_mac:
            dry_result["would_set_boot_interface_mac"] = source_boot_mac
            dry_result["boot_interface_db_configured"] = db_url is not None
        return dry_result

    deleted: list[dict[str, Any]] = []
    errors: list[str] = []
    for iface in to_delete:
        iface_id = iface["id"]
        try:
            target_client.delete(f"nodes/{target_sid}/interfaces/{iface_id}")
            deleted.append({"id": iface_id, "name": iface["name"], "mac": iface["mac_address"]})
        except Exception as exc:
            errors.append(f"delete {iface['name']} (id={iface_id}): {exc}")

    created: list[dict[str, Any]] = []
    for iface in to_create:
        create_data: dict[str, Any] = {
            "mac_address": iface["mac_address"],
            "name": iface["name"],
        }
        if iface.get("effective_mtu") and iface["effective_mtu"] != 1500:
            create_data["mtu"] = str(iface["effective_mtu"])
        try:
            result = target_client.post(
                f"nodes/{target_sid}/interfaces",
                data=create_data,
                params={"op": "create_physical"},
            )
            new_id = result.get("id") if isinstance(result, dict) else None
            created.append({"id": new_id, "name": iface["name"], "mac": iface["mac_address"]})
        except Exception as exc:
            errors.append(f"create {iface['name']} ({iface['mac_address']}): {exc}")

    renamed: list[dict[str, Any]] = []
    for entry in to_rename:
        try:
            target_client.put(
                f"nodes/{target_sid}/interfaces/{entry['id']}",
                data={"name": entry["new_name"]},
            )
            renamed.append(entry)
        except Exception as exc:
            errors.append(
                f"rename {entry['old_name']}->{entry['new_name']} (id={entry['id']}): {exc}"
            )

    # Set boot_interface_id to match source's boot interface (by MAC)
    boot_iface_set = False
    source_boot_mac = _get_boot_interface_mac(source_machine)
    if source_boot_mac and db_url:
        tgt_refreshed = target_client.get(f"machines/{target_sid}")
        tgt_iface_map = {
            (i.get("mac_address") or "").lower(): i.get("id")
            for i in (tgt_refreshed.get("interface_set") or [])
            if i.get("type") == "physical"
        }
        boot_iface_id = tgt_iface_map.get(source_boot_mac)
        if boot_iface_id:
            try:
                import psycopg

                with psycopg.connect(db_url, connect_timeout=10) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE maasserver_node SET boot_interface_id = %s WHERE system_id = %s",
                            (boot_iface_id, target_sid),
                        )
                    conn.commit()
                boot_iface_set = True
            except Exception as exc:
                errors.append(f"set boot_interface_id: {exc}")

    result_out: dict[str, Any] = {
        "ok": not errors,
        "deleted": deleted,
        "created": created,
        "renamed": renamed,
        "skipped": [{"name": i["name"], "mac": i["mac_address"]} for i in to_skip],
    }
    if boot_iface_set:
        result_out["boot_interface_mac"] = source_boot_mac
    if errors:
        result_out["errors"] = errors
    return result_out


def _migrate_sync_metadata(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Copy machine metadata (hostname, arch, zone, pool, cpu, memory, desc) from source."""
    source = source_client.get(f"machines/{source_sid}")

    payload: dict[str, str] = {}
    for key in _METADATA_FIELDS:
        val = source.get(key)
        if val is not None and str(val).strip():
            payload[key] = str(val).strip()

    for key in _METADATA_ADMIN_FIELDS:
        raw = source.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            name = raw.get("name")
            if name:
                payload[key] = str(name)
        elif str(raw).strip() and str(raw).strip() != "0":
            payload[key] = str(raw).strip()

    if not payload:
        return {"ok": True, "skipped": True, "reason": "no_metadata_to_copy"}

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_update": payload,
        }

    errors: list[str] = []
    auto_created: list[str] = []

    if "zone" in payload:
        try:
            existing_zones = target_client.get("zones")
            zone_names = {
                z.get("name")
                for z in (existing_zones if isinstance(existing_zones, list) else [])
                if isinstance(z, dict)
            }
            if payload["zone"] not in zone_names:
                target_client.post("zones", data={"name": payload["zone"]})
                auto_created.append(f"zone:{payload['zone']}")
        except Exception as exc:
            errors.append(f"auto-create zone '{payload['zone']}': {exc}")

    if "pool" in payload:
        try:
            existing_pools = target_client.get("resourcepools")
            pool_names = {
                p.get("name")
                for p in (existing_pools if isinstance(existing_pools, list) else [])
                if isinstance(p, dict)
            }
            if payload["pool"] not in pool_names:
                target_client.post("resourcepools", data={"name": payload["pool"]})
                auto_created.append(f"pool:{payload['pool']}")
        except Exception as exc:
            errors.append(f"auto-create pool '{payload['pool']}': {exc}")

    try:
        target_client.put(f"machines/{target_sid}", data=payload)
    except Exception as exc:
        errors.append(f"PUT metadata: {exc}")

    result: dict[str, Any] = {
        "ok": not errors,
        "updated_fields": sorted(payload.keys()),
    }
    if auto_created:
        result["auto_created"] = auto_created
    if errors:
        result["errors"] = errors
    return result


def _migrate_sync_disks(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Copy physical block devices from source to target via the MAAS API.

    Matches existing target disks by serial (if available) or name to avoid
    duplicates.  Creates missing disks via ``POST /nodes/{sid}/blockdevices/``.
    The target machine must be in a state that allows block device creation.

    When ``db_url`` is provided and disks have partitions/filesystems on source,
    those are replicated via direct DB inserts (the MAAS API does not support
    creating partitions on non-deployed machines).
    """
    target_pre = target_client.get(f"machines/{target_sid}")
    target_status = target_pre.get("status_name") or ""
    if target_status not in _IFACE_MUTABLE_STATUSES:
        return {
            "ok": False,
            "error": (
                f"Target {target_sid} is in '{target_status}'. "
                f"Must be one of {', '.join(_IFACE_MUTABLE_STATUSES)} to create block devices."
            ),
        }

    source = source_client.get(f"machines/{source_sid}")
    source_disks = [
        d
        for d in (source.get("blockdevice_set") or source.get("physicalblockdevice_set") or [])
        if isinstance(d, dict) and d.get("type", "physical") == "physical"
    ]

    target = target_client.get(f"machines/{target_sid}")
    target_disks = [
        d
        for d in (target.get("blockdevice_set") or target.get("physicalblockdevice_set") or [])
        if isinstance(d, dict)
    ]
    target_serials = {(d.get("serial") or "").lower() for d in target_disks if d.get("serial")}
    target_names = {d.get("name") or "" for d in target_disks}

    to_create: list[dict[str, Any]] = []
    source_disk_by_serial: dict[str, dict] = {}
    skipped: list[str] = []

    for disk in source_disks:
        serial = (disk.get("serial") or "").strip()
        name = disk.get("name") or ""
        if serial:
            source_disk_by_serial[serial.lower()] = disk
        if serial and serial.lower() in target_serials:
            skipped.append(f"{name} (serial={serial[:12]}..., already on target)")
            continue
        if not serial and name in target_names:
            skipped.append(f"{name} (name match, already on target)")
            continue
        entry: dict[str, str] = {"name": name}
        if disk.get("model"):
            entry["model"] = str(disk["model"])
        if serial:
            entry["serial"] = serial
        if disk.get("size"):
            entry["size"] = str(disk["size"])
        if disk.get("block_size"):
            entry["block_size"] = str(disk["block_size"])
        if disk.get("id_path"):
            entry["id_path"] = str(disk["id_path"])
        to_create.append(entry)

    # Partition plan: source disks that have partitions
    partition_plan: list[dict[str, Any]] = []
    for disk in source_disks:
        partitions = disk.get("partitions") or []
        if not partitions:
            continue
        pt_table = disk.get("partition_table_type") or "GPT"
        serial = (disk.get("serial") or "").strip()
        parts_info: list[dict[str, Any]] = []
        for part_idx, p in enumerate(partitions, start=1):
            pinfo: dict[str, Any] = {
                "uuid": p.get("uuid") or "",
                "size": p.get("size") or 0,
                "bootable": p.get("bootable", False),
                "index": p.get("index") or part_idx,
            }
            fs = p.get("filesystem")
            if isinstance(fs, dict) and fs.get("fstype"):
                pinfo["filesystem"] = {
                    "uuid": fs.get("uuid") or "",
                    "fstype": fs["fstype"],
                    "mount_point": fs.get("mount_point") or "",
                    "mount_options": fs.get("mount_options") or "",
                }
            parts_info.append(pinfo)
        partition_plan.append(
            {
                "disk_serial": serial,
                "disk_name": disk.get("name") or "",
                "table_type": pt_table,
                "partitions": parts_info,
            }
        )

    if dry_run:
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "would_create": to_create,
            "skipped": skipped,
        }
        if partition_plan:
            result["would_create_partitions"] = partition_plan
            result["partition_db_configured"] = db_url is not None
        return result

    errors: list[str] = []
    created: list[dict[str, Any]] = []
    for entry in to_create:
        try:
            res = target_client.post(f"nodes/{target_sid}/blockdevices", data=entry)
            new_id = res.get("id") if isinstance(res, dict) else None
            created.append(
                {"id": new_id, "name": entry.get("name", ""), "serial": entry.get("serial", "")}
            )
        except Exception as exc:
            errors.append(f"create disk {entry.get('name', '?')}: {exc}")

    # Partition/filesystem sync via DB
    partitions_created: list[dict[str, Any]] = []
    if partition_plan and db_url:
        partitions_created, part_errors = _sync_partitions_via_db(
            target_client, target_sid, partition_plan, db_url
        )
        errors.extend(part_errors)

    out: dict[str, Any] = {"ok": not errors, "created": created, "skipped": skipped}
    if partitions_created:
        out["partitions_created"] = partitions_created
    if errors:
        out["errors"] = errors
    return out


def _sync_partitions_via_db(
    target_client: MaasRestClient,
    target_sid: str,
    partition_plan: list[dict[str, Any]],
    db_url: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Create partition tables, partitions, and filesystems via direct DB access."""
    import psycopg

    created: list[dict[str, Any]] = []
    errors: list[str] = []

    # Re-read target to get blockdevice IDs after API creation
    target = target_client.get(f"machines/{target_sid}")
    target_disks = target.get("blockdevice_set") or target.get("physicalblockdevice_set") or []
    tgt_disk_by_serial: dict[str, int] = {}
    tgt_disk_by_name: dict[str, int] = {}
    for d in target_disks:
        if isinstance(d, dict):
            did = d.get("id")
            if did is None:
                continue
            s = (d.get("serial") or "").strip().lower()
            if s:
                tgt_disk_by_serial[s] = did
            n = d.get("name") or ""
            if n:
                tgt_disk_by_name[n] = did

    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM maasserver_node WHERE system_id = %s",
                    (target_sid,),
                )
                row = cur.fetchone()
                if not row:
                    return [], [f"system_id '{target_sid}' not found in DB"]
                node_id: int = row[0]

                cur.execute(
                    "SELECT id FROM maasserver_nodeconfig WHERE node_id = %s ORDER BY id DESC LIMIT 1",
                    (node_id,),
                )
                nc_row = cur.fetchone()
                if not nc_row:
                    return [], [f"no nodeconfig found for node_id={node_id}"]
                node_config_id: int = nc_row[0]

                for plan in partition_plan:
                    serial = (plan.get("disk_serial") or "").lower()
                    disk_name = plan.get("disk_name") or ""
                    block_id = tgt_disk_by_serial.get(serial) or tgt_disk_by_name.get(disk_name)
                    if not block_id:
                        errors.append(f"target disk not found for serial={serial} name={disk_name}")
                        continue

                    try:
                        cur.execute(
                            "SELECT id FROM maasserver_partitiontable WHERE block_device_id = %s",
                            (block_id,),
                        )
                        pt_row = cur.fetchone()
                        if pt_row:
                            pt_id: int = pt_row[0]
                            cur.execute(
                                "UPDATE maasserver_partitiontable SET table_type = %s, updated = NOW() WHERE id = %s",
                                (plan["table_type"], pt_id),
                            )
                        else:
                            cur.execute(
                                """INSERT INTO maasserver_partitiontable
                                    (created, updated, table_type, block_device_id)
                                VALUES (NOW(), NOW(), %s, %s) RETURNING id""",
                                (plan["table_type"], block_id),
                            )
                            pt_id = cur.fetchone()[0]  # type: ignore[index]

                        for pinfo in plan.get("partitions") or []:
                            cur.execute(
                                """
                                INSERT INTO maasserver_partition
                                    (created, updated, uuid, size, bootable, partition_table_id, "index", tags)
                                VALUES (NOW(), NOW(), %(uuid)s, %(size)s, %(boot)s, %(pt)s, %(idx)s, '{}')
                                ON CONFLICT (partition_table_id, "index") DO UPDATE
                                    SET size = EXCLUDED.size, bootable = EXCLUDED.bootable, updated = NOW()
                                RETURNING id
                                """,
                                {
                                    "uuid": pinfo.get("uuid") or "",
                                    "size": pinfo.get("size") or 0,
                                    "boot": pinfo.get("bootable", False),
                                    "pt": pt_id,
                                    "idx": pinfo.get("index", 0),
                                },
                            )
                            part_id: int = cur.fetchone()[0]  # type: ignore[index]

                            fs = pinfo.get("filesystem")
                            if isinstance(fs, dict) and fs.get("fstype"):
                                cur.execute(
                                    """
                                    INSERT INTO maasserver_filesystem
                                        (created, updated, uuid, fstype, mount_point,
                                         mount_options, partition_id, node_config_id,
                                         block_device_id, filesystem_group_id, cache_set_id,
                                         acquired, label, create_params)
                                    VALUES (NOW(), NOW(), %(uuid)s, %(fst)s, %(mp)s,
                                            %(mo)s, %(pid)s, %(ncid)s,
                                            NULL, NULL, NULL, false, '', '')
                                    ON CONFLICT (partition_id, acquired) DO UPDATE
                                        SET fstype = EXCLUDED.fstype,
                                            mount_point = EXCLUDED.mount_point,
                                            updated = NOW()
                                    """,
                                    {
                                        "uuid": fs.get("uuid") or "",
                                        "fst": fs["fstype"],
                                        "mp": fs.get("mount_point") or "",
                                        "mo": fs.get("mount_options") or "",
                                        "pid": part_id,
                                        "ncid": node_config_id,
                                    },
                                )

                        created.append(
                            {
                                "disk": disk_name or serial,
                                "partition_table_id": pt_id,
                                "partition_count": len(plan.get("partitions") or []),
                            }
                        )
                    except Exception as exc:
                        errors.append(
                            f"partitions for disk {disk_name}: {type(exc).__name__}: {exc}"
                        )

            conn.commit()
    except Exception as exc:
        errors.append(f"partition DB connect: {type(exc).__name__}: {exc}")

    return created, errors


def _migrate_sync_tags(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Ensure source machine's tags exist on target and machine is associated.

    Creates manual tags (no XPath definition) if they don't exist on the
    target MAAS, then adds the machine to each tag via ``op=update_nodes``.
    """
    source = source_client.get(f"machines/{source_sid}")
    source_tags: list[str] = source.get("tag_names") or []
    if not source_tags:
        return {"ok": True, "skipped": True, "reason": "source_has_no_tags"}

    target = target_client.get(f"machines/{target_sid}")
    target_tags: list[str] = target.get("tag_names") or []
    existing_target_tags_raw = target_client.get("tags")
    if isinstance(existing_target_tags_raw, list):
        known_tags = {t.get("name") for t in existing_target_tags_raw if isinstance(t, dict)}
    elif isinstance(existing_target_tags_raw, dict):
        known_tags = {
            t.get("name")
            for t in existing_target_tags_raw.get(
                "results", existing_target_tags_raw.get("items", [])
            )
            if isinstance(t, dict)
        }
    else:
        known_tags = set()

    to_add = [t for t in source_tags if t not in target_tags]
    already = [t for t in source_tags if t in target_tags]

    if dry_run:
        tags_to_create = [t for t in to_add if t not in known_tags]
        return {
            "ok": True,
            "dry_run": True,
            "would_add_to_machine": to_add,
            "would_create_tags": tags_to_create,
            "already_on_machine": already,
        }

    tags_created: list[str] = []
    errors: list[str] = []
    for tag_name in to_add:
        if tag_name not in known_tags:
            try:
                target_client.post("tags", data={"name": tag_name})
                tags_created.append(tag_name)
                known_tags.add(tag_name)
            except RuntimeError as exc:
                errors.append(f"create tag '{tag_name}': {exc}")

    tags_added: list[str] = []
    for tag_name in to_add:
        if tag_name in known_tags:
            try:
                target_client.post(
                    f"tags/{tag_name}",
                    data={"add": target_sid},
                    params={"op": "update_nodes"},
                )
                tags_added.append(tag_name)
            except RuntimeError as exc:
                errors.append(f"add machine to tag '{tag_name}': {exc}")

    return {
        "ok": not errors,
        "tags_created": tags_created,
        "tags_added": tags_added,
        "already_on_machine": already,
        **({"errors": errors} if errors else {}),
    }


def _migrate_sync_hardware_info(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Copy hardware_info (NodeMetadata) from source to target via direct DB access.

    The MAAS API does not expose a write endpoint for NodeMetadata keys
    (system_vendor, cpu_model, etc.).  This helper reads them from the source
    API and upserts into the target's ``maasserver_nodemetadata`` table.

    Requires a PostgreSQL connection string for the *target* MAAS database.
    """
    source = source_client.get(f"machines/{source_sid}")
    hw_info: dict[str, str] = source.get("hardware_info") or {}

    entries = {k: v for k, v in hw_info.items() if v and v.lower() not in ("unknown", "")}
    if not entries:
        return {"ok": True, "skipped": True, "reason": "no_hardware_info_on_source"}

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_write": entries,
            "db_configured": db_url is not None,
        }

    if not db_url:
        return {
            "ok": False,
            "error": (
                "No database URL configured for target MAAS. "
                "Set MAAS_DB_URL or MAAS_{SITE}_DB_URL to enable hardware_info sync."
            ),
            "would_write_keys": sorted(entries.keys()),
        }

    import psycopg

    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM maasserver_node WHERE system_id = %s",
                    (target_sid,),
                )
                row = cur.fetchone()
                if not row:
                    return {
                        "ok": False,
                        "error": f"system_id '{target_sid}' not found in maasserver_node",
                    }
                target_node_id: int = row[0]

                upserted: list[str] = []
                for key, value in entries.items():
                    cur.execute(
                        """
                        INSERT INTO maasserver_nodemetadata (node_id, key, value, created, updated)
                        VALUES (%(node_id)s, %(key)s, %(value)s, NOW(), NOW())
                        ON CONFLICT (node_id, key) DO UPDATE SET value = EXCLUDED.value, updated = NOW()
                        """,
                        {"node_id": target_node_id, "key": key, "value": value},
                    )
                    upserted.append(key)
            conn.commit()
    except Exception as exc:
        return {"ok": False, "error": f"DB operation failed: {type(exc).__name__}: {exc}"}

    return {"ok": True, "upserted": upserted, "target_node_id": target_node_id}


def _migrate_sync_numa_and_devices(
    source_client: MaasRestClient,
    source_sid: str,
    target_client: MaasRestClient,
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Sync NUMA nodes, PCI/USB devices, interface NUMA refs, and speed fields via DB.

    Reads NUMA topology and device list from source API, then upserts into the
    target DB.  Also patches interface ``link_speed``, ``interface_speed``, and
    ``link_connected`` by MAC match.
    """
    source_machine = source_client.get(f"machines/{source_sid}")
    numanode_set = source_machine.get("numanode_set") or []

    # Source interface speed/NUMA data (keyed by lowercase MAC)
    src_iface_by_mac: dict[str, dict[str, Any]] = {}
    src_iface_by_id: dict[int, str] = {}
    for iface in source_machine.get("interface_set") or []:
        mac = (iface.get("mac_address") or "").lower()
        if mac and iface.get("type") == "physical":
            src_iface_by_mac[mac] = {
                "numa_node": iface.get("numa_node"),
                "link_speed": iface.get("link_speed"),
                "interface_speed": iface.get("interface_speed"),
                "link_connected": iface.get("link_connected"),
                "sriov_max_vf": iface.get("sriov_max_vf", 0),
                "vendor": iface.get("vendor"),
                "product": iface.get("product"),
                "firmware_version": iface.get("firmware_version"),
            }
            if iface.get("id") is not None:
                src_iface_by_id[iface["id"]] = mac

    # Source devices
    try:
        source_devices = source_client.get(f"nodes/{source_sid}/devices")
        if not isinstance(source_devices, list):
            source_devices = []
    except Exception:
        source_devices = []

    numa_plan = [
        {"index": n.get("index"), "memory": n.get("memory", 0), "cores": n.get("cores") or []}
        for n in numanode_set
        if n.get("index") is not None
    ]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "db_configured": db_url is not None,
            "would_sync_numa_nodes": len(numa_plan),
            "would_sync_devices": len(source_devices),
            "would_patch_iface_speed": len(src_iface_by_mac),
        }

    if not db_url:
        return {
            "ok": False,
            "error": (
                "No database URL configured for target MAAS. "
                "Set MAAS_DB_URL or MAAS_{SITE}_DB_URL to enable NUMA/device sync."
            ),
        }

    import psycopg

    errors: list[str] = []
    summary: dict[str, Any] = {}

    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                # Resolve target node_id
                cur.execute(
                    "SELECT id FROM maasserver_node WHERE system_id = %s",
                    (target_sid,),
                )
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": f"system_id '{target_sid}' not found in DB"}
                node_id: int = row[0]

                cur.execute(
                    "SELECT id FROM maasserver_nodeconfig WHERE node_id = %s ORDER BY id DESC LIMIT 1",
                    (node_id,),
                )
                nc_row = cur.fetchone()
                if not nc_row:
                    return {"ok": False, "error": f"no nodeconfig for node_id={node_id}"}
                node_config_id: int = nc_row[0]

                # --- NUMA nodes ---
                numa_id_by_index: dict[int, int] = {}
                for n in numa_plan:
                    idx = n["index"]
                    mem = n["memory"]
                    cores = n["cores"]
                    cores_arr = cores if isinstance(cores, list) else []
                    try:
                        cur.execute(
                            """
                            INSERT INTO maasserver_numanode (created, updated, index, memory, cores, node_id)
                            VALUES (NOW(), NOW(), %(idx)s, %(mem)s, %(cores)s, %(nid)s)
                            ON CONFLICT (node_id, index) DO UPDATE
                                SET memory = EXCLUDED.memory, cores = EXCLUDED.cores, updated = NOW()
                            RETURNING id
                            """,
                            {"idx": idx, "mem": mem, "cores": cores_arr, "nid": node_id},
                        )
                        numa_id_by_index[idx] = cur.fetchone()[0]  # type: ignore[index]
                    except Exception as exc:
                        errors.append(f"upsert NUMA index={idx}: {exc}")

                summary["numa_upserted"] = len(numa_id_by_index)

                # --- Interface NUMA + speed fields ---
                target_machine = target_client.get(f"machines/{target_sid}")
                iface_updates = 0
                for tiface in target_machine.get("interface_set") or []:
                    if tiface.get("type") != "physical":
                        continue
                    tmac = (tiface.get("mac_address") or "").lower()
                    tid = tiface.get("id")
                    src_info = src_iface_by_mac.get(tmac)
                    if not src_info or not tid:
                        continue

                    set_parts: list[str] = []
                    params: dict[str, Any] = {"iid": tid}

                    src_numa_idx = src_info.get("numa_node")
                    if src_numa_idx is not None and src_numa_idx in numa_id_by_index:
                        set_parts.append("numa_node_id = %(nid)s")
                        params["nid"] = numa_id_by_index[src_numa_idx]

                    for field in ("link_speed", "interface_speed"):
                        val = src_info.get(field)
                        if val is not None:
                            set_parts.append(f"{field} = %({field})s")
                            params[field] = val

                    lc = src_info.get("link_connected")
                    if lc is not None:
                        set_parts.append("link_connected = %(lc)s")
                        params["lc"] = bool(lc)

                    sriov_max_vf = src_info.get("sriov_max_vf")
                    if sriov_max_vf is not None:
                        set_parts.append("sriov_max_vf = %(sriov_max_vf)s")
                        params["sriov_max_vf"] = int(sriov_max_vf)

                    vendor = src_info.get("vendor")
                    if vendor is not None:
                        set_parts.append("vendor = %(vendor)s")
                        params["vendor"] = str(vendor)

                    product = src_info.get("product")
                    if product is not None:
                        set_parts.append("product = %(product)s")
                        params["product"] = str(product)

                    fw = src_info.get("firmware_version")
                    if fw is not None:
                        set_parts.append("firmware_version = %(fw)s")
                        params["fw"] = str(fw)

                    if set_parts:
                        try:
                            cur.execute(
                                f"UPDATE maasserver_interface SET {', '.join(set_parts)} WHERE id = %(iid)s",
                                params,
                            )
                            iface_updates += 1
                        except Exception as exc:
                            errors.append(f"update iface id={tid} mac={tmac}: {exc}")

                summary["iface_updated"] = iface_updates

                # --- Block device NUMA assignments ---
                bd_updates = 0
                if numa_id_by_index:
                    # Default NUMA = highest index (fallback for disks without explicit mapping)
                    default_numa_id = numa_id_by_index.get(max(numa_id_by_index))
                    cur.execute(
                        """
                        SELECT pbd.blockdevice_ptr_id
                        FROM maasserver_physicalblockdevice pbd
                        JOIN maasserver_blockdevice bd ON bd.id = pbd.blockdevice_ptr_id
                        WHERE bd.node_config_id = %s
                        """,
                        (node_config_id,),
                    )
                    for (bd_id,) in cur.fetchall():
                        try:
                            cur.execute(
                                "UPDATE maasserver_physicalblockdevice SET numa_node_id = %s WHERE blockdevice_ptr_id = %s",
                                (default_numa_id, bd_id),
                            )
                            bd_updates += 1
                        except Exception as exc:
                            errors.append(f"update blockdevice numa bd_id={bd_id}: {exc}")

                summary["blockdevice_numa_updated"] = bd_updates

                # --- PCI/USB devices ---
                if source_devices:
                    try:
                        cur.execute(
                            "DELETE FROM maasserver_nodedevicevpd WHERE node_device_id IN (SELECT id FROM maasserver_nodedevice WHERE node_config_id = %s)",
                            (node_config_id,),
                        )
                        cur.execute(
                            "DELETE FROM maasserver_nodedevice WHERE node_config_id = %s",
                            (node_config_id,),
                        )
                        deleted_count = cur.rowcount
                    except Exception as exc:
                        deleted_count = 0
                        errors.append(f"delete existing nodedevices: {exc}")

                    inserted = 0
                    for dev in source_devices:
                        if not isinstance(dev, dict):
                            continue
                        dev_numa_idx = dev.get("numa_node", 0) or 0
                        dev_numa_id = numa_id_by_index.get(dev_numa_idx) or numa_id_by_index.get(0)
                        if dev_numa_id is None and numa_id_by_index:
                            dev_numa_id = next(iter(numa_id_by_index.values()))
                        if dev_numa_id is None:
                            errors.append(
                                f"no NUMA node for device bus={dev.get('bus_number')} pci={dev.get('pci_address')}"
                            )
                            continue

                        try:
                            cur.execute(
                                """
                                INSERT INTO maasserver_nodedevice
                                    (created, updated, bus, hardware_type, vendor_id, product_id,
                                     vendor_name, product_name, commissioning_driver,
                                     bus_number, device_number, pci_address,
                                     numa_node_id, physical_blockdevice_id, physical_interface_id,
                                     node_config_id)
                                VALUES
                                    (NOW(), NOW(), %(bus)s, %(hwt)s, %(vid)s, %(pid)s,
                                     %(vn)s, %(pn)s, %(cd)s,
                                     %(bn)s, %(dn)s, %(pa)s,
                                     %(nnid)s, NULL, NULL,
                                     %(ncid)s)
                                """,
                                {
                                    "bus": dev.get("bus", 0),
                                    "hwt": dev.get("hardware_type", 0),
                                    "vid": dev.get("vendor_id") or "",
                                    "pid": dev.get("product_id") or "",
                                    "vn": dev.get("vendor_name") or "",
                                    "pn": dev.get("product_name") or "",
                                    "cd": dev.get("commissioning_driver") or "",
                                    "bn": dev.get("bus_number", 0) or 0,
                                    "dn": dev.get("device_number", 0) or 0,
                                    "pa": dev.get("pci_address") or None,
                                    "nnid": dev_numa_id,
                                    "ncid": node_config_id,
                                },
                            )
                            inserted += 1
                        except Exception as exc:
                            errors.append(f"insert device pci={dev.get('pci_address')}: {exc}")

                    summary["devices_deleted"] = deleted_count
                    summary["devices_inserted"] = inserted

                    # --- Link PCI network devices to interfaces (physical_interface_id) ---
                    pci_nic_linked = 0
                    for dev in source_devices:
                        if not isinstance(dev, dict):
                            continue
                        src_phys_iface_raw = dev.get("physical_interface")
                        src_phys_iface_id = (
                            src_phys_iface_raw.get("id")
                            if isinstance(src_phys_iface_raw, dict)
                            else src_phys_iface_raw
                        )
                        dev_pci = dev.get("pci_address")
                        if not src_phys_iface_id or not dev_pci:
                            continue
                        src_mac = src_iface_by_id.get(src_phys_iface_id)
                        if not src_mac:
                            continue
                        try:
                            cur.execute(
                                """
                                UPDATE maasserver_nodedevice nd
                                SET physical_interface_id = iface.id
                                FROM maasserver_interface iface
                                WHERE nd.node_config_id = %s
                                  AND nd.pci_address = %s
                                  AND iface.node_config_id = %s
                                  AND LOWER(iface.mac_address) = %s
                                  AND iface.type = 'physical'
                                """,
                                (node_config_id, dev_pci, node_config_id, src_mac),
                            )
                            if cur.rowcount:
                                pci_nic_linked += 1
                        except Exception as exc:
                            errors.append(f"link device pci={dev_pci} to iface: {exc}")
                    summary["pci_nic_linked"] = pci_nic_linked

                # --- BMC privilege_level = ADMIN ---
                try:
                    cur.execute(
                        """
                        UPDATE maasserver_bmc
                        SET power_parameters = power_parameters || '{"privilege_level": "ADMIN"}'::jsonb
                        WHERE id = (SELECT bmc_id FROM maasserver_node WHERE system_id = %s)
                        """,
                        (target_sid,),
                    )
                    summary["bmc_privilege_level_set"] = bool(cur.rowcount)
                except Exception as exc:
                    errors.append(f"set BMC privilege_level: {exc}")

            conn.commit()
    except Exception as exc:
        errors.append(f"NUMA/device DB connect: {type(exc).__name__}: {exc}")

    return {
        "ok": not errors,
        **summary,
        **({"errors": errors} if errors else {}),
    }


def _migrate_set_commissioning_scriptset(
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Insert a minimal commissioning ScriptSet so the MAAS UI doesn't show
    "Not yet commissioned" for Deployed machines.

    Creates a row in ``maasserver_scriptset`` with ``result_type=0``
    (commissioning) and points ``current_commissioning_script_set_id`` at it.
    Skips if one already exists.
    """
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_create_scriptset": True,
            "db_configured": db_url is not None,
        }

    if not db_url:
        return {
            "ok": False,
            "error": "No database URL configured; cannot set commissioning script set.",
        }

    import psycopg

    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, current_commissioning_script_set_id "
                    "FROM maasserver_node WHERE system_id = %s",
                    (target_sid,),
                )
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": f"system_id '{target_sid}' not found in DB"}
                node_id, existing_ss_id = row[0], row[1]

                if existing_ss_id:
                    return {
                        "ok": True,
                        "skipped": True,
                        "reason": "already_has_commissioning_scriptset",
                    }

                cur.execute(
                    """
                    INSERT INTO maasserver_scriptset
                        (last_ping, node_id, result_type,
                         power_state_before_transition, tags)
                    VALUES (NULL, %s, 0, '', '{}')
                    RETURNING id
                    """,
                    (node_id,),
                )
                ss_id: int = cur.fetchone()[0]  # type: ignore[index]

                cur.execute(
                    """
                    INSERT INTO maasserver_scriptresult
                        (created, updated, status, exit_status, script_name,
                         stdout, stderr, result, output,
                         ended, started, parameters, suppressed,
                         script_set_id, script_id, script_version_id,
                         physical_blockdevice_id, interface_id)
                    VALUES (NOW(), NOW(), 2, 0, '50-maas-01-commissioning',
                            '', '', '', '',
                            NOW(), NOW(), '{}', false,
                            %s, NULL, NULL, NULL, NULL)
                    """,
                    (ss_id,),
                )

                cur.execute(
                    "UPDATE maasserver_node SET current_commissioning_script_set_id = %s WHERE id = %s",
                    (ss_id, node_id),
                )
            conn.commit()
    except Exception as exc:
        return {"ok": False, "error": f"scriptset DB: {type(exc).__name__}: {exc}"}

    return {"ok": True, "script_set_id": ss_id}


def _migrate_set_deployed(
    target_sid: str,
    *,
    dry_run: bool,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Transition a migrated node to Deployed status (status=6) via direct DB.

    Also assigns the machine to the first superuser (admin) as owner.
    """
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_set_status": 6,
            "would_assign_owner": True,
            "db_configured": db_url is not None,
        }

    if not db_url:
        return {"ok": False, "error": "No database URL configured; cannot set deployed state."}

    import psycopg

    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM auth_user WHERE is_superuser = true ORDER BY id LIMIT 1",
                )
                admin_row = cur.fetchone()
                if not admin_row:
                    return {"ok": False, "error": "No superuser found in auth_user"}
                admin_id: int = admin_row[0]

                cur.execute(
                    "UPDATE maasserver_node SET status = 6, owner_id = %s WHERE system_id = %s",
                    (admin_id, target_sid),
                )
                if cur.rowcount == 0:
                    return {"ok": False, "error": f"system_id '{target_sid}' not found in DB"}

            conn.commit()
    except Exception as exc:
        return {"ok": False, "error": f"set-deployed DB: {type(exc).__name__}: {exc}"}

    return {"ok": True, "status": 6, "owner_id": admin_id}


# ---------------------------------------------------------------------------
# Elicitation helper for destructive write confirmations
# ---------------------------------------------------------------------------


async def _confirm_or_proceed(ctx: Context, message: str) -> ToolResult | None:
    """Ask the user for confirmation via MCP elicitation. Returns a cancelled
    ToolResult if declined/cancelled, or None to proceed. Silently proceeds
    if the client does not support elicitation."""
    try:
        result = await ctx.elicit(message, response_type=None)
        if result.action == "decline":
            return ToolResult(
                content="Operation declined by user.",
                structured_content={"cancelled": True, "reason": "User declined"},
            )
        if result.action == "cancel":
            return ToolResult(
                content="Operation cancelled by user.",
                structured_content={"cancelled": True, "reason": "User cancelled"},
            )
    except Exception:
        pass
    return None


def _tool_result(data: dict[str, Any]) -> ToolResult:
    """Create a ToolResult with both content (JSON text) and structured_content."""
    return ToolResult(content=json.dumps(data, indent=2), structured_content=data)


# ---------------------------------------------------------------------------
# Operation state maps for wait/poll mode
# ---------------------------------------------------------------------------

_OP_STATES: dict[str, dict[str, list[str]]] = {
    "commission": {
        "success": ["Ready"],
        "in_progress": ["Commissioning"],
        "failure": ["Failed commissioning"],
    },
    "deploy": {
        "success": ["Deployed"],
        "in_progress": ["Deploying"],
        "failure": ["Failed deployment"],
    },
    "release": {
        "success": ["Ready"],
        "in_progress": ["Releasing", "Disk erasing"],
        "failure": ["Failed releasing", "Failed disk erasing"],
    },
    "power_on": {"success_power": ["on"], "failure": []},
    "power_off": {"success_power": ["off"], "failure": []},
    "power_cycle": {"success_power": ["on"], "failure": []},
    "exit_rescue_mode": {
        "success": ["Ready", "Exiting rescue mode"],
        "in_progress": ["Exiting rescue mode"],
        "failure": ["Failed to exit rescue mode"],
    },
    "mark_broken": {"success": ["Broken"]},
    "mark_fixed": {"success": ["Ready"]},
}

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("MAAS")

maas_clients: dict[str, MaasRestClient] = {}
maas_db_urls: dict[str, str] = {}
netbox: NetboxClient | None = None


def _get_db_url(instance: str) -> str | None:
    """Resolve the PostgreSQL connection URL for *instance*, falling back to 'default'."""
    return maas_db_urls.get(instance) or maas_db_urls.get("default")


def _get_netbox() -> NetboxClient:
    """Return the NetBox client, raising ToolError if unconfigured."""
    if netbox is None:
        raise ToolError("NetBox is not configured. Set NETBOX_URL and NETBOX_TOKEN env vars.")
    return netbox


# ---------------------------------------------------------------------------
# Completions (via low-level MCP server — FastMCP has no high-level API yet)
# ---------------------------------------------------------------------------

_PROMPT_COMPLETABLE_ARGS: dict[str, dict[str, str]] = {
    "investigate_machine": {"system_id": "system_id", "hostname": "hostname"},
    "audit_drift": {"baseline_system_id": "system_id"},
    "sync_bmc_credentials": {"system_id": "system_id"},
}


def _complete_system_ids(prefix: str) -> list[str]:
    """Return system_ids matching the given prefix across all instances."""
    results: list[str] = []
    for client in maas_clients.values():
        try:
            for m in _normalize_list_response(client.get("machines")):
                sid = m.get("system_id", "")
                if sid and sid.startswith(prefix):
                    results.append(sid)
        except Exception:
            continue
    return sorted(set(results))[:100]


def _complete_hostnames(prefix: str) -> list[str]:
    """Return hostnames matching the given prefix across all instances."""
    results: list[str] = []
    for client in maas_clients.values():
        try:
            for m in _normalize_list_response(client.get("machines")):
                name = m.get("hostname", "")
                if name and name.startswith(prefix):
                    results.append(name)
        except Exception:
            continue
    return sorted(set(results))[:100]


@mcp._mcp_server.completion()  # type: ignore[no-untyped-call,untyped-decorator]
async def _handle_completion(
    ref: mcp_types.PromptReference | mcp_types.ResourceTemplateReference,
    argument: mcp_types.CompletionArgument,
    context: mcp_types.CompletionContext | None,
) -> mcp_types.Completion | None:
    """Provide auto-complete suggestions for prompt and resource template arguments."""
    if isinstance(ref, mcp_types.PromptReference):
        arg_map = _PROMPT_COMPLETABLE_ARGS.get(ref.name, {})
        kind = arg_map.get(argument.name)
        if kind == "system_id":
            values = _complete_system_ids(argument.value)
            return mcp_types.Completion(values=values, hasMore=len(values) >= 100)
        if kind == "hostname":
            values = _complete_hostnames(argument.value)
            return mcp_types.Completion(values=values, hasMore=len(values) >= 100)

    if isinstance(ref, mcp_types.ResourceTemplateReference):
        if argument.name == "instance":
            instances = sorted(n for n in maas_clients if n.startswith(argument.value))
            return mcp_types.Completion(values=instances)

    return None


def get_client(instance: str) -> MaasRestClient:
    """Get MAAS client for the specified instance."""
    if instance not in maas_clients:
        raise RuntimeError(
            f"MAAS instance '{instance}' not configured. Available instances: {list(maas_clients.keys())}"
        )
    return maas_clients[instance]


# ===== MCP Resources =====


@mcp.resource("maas://instances")
def resource_instances() -> str:
    """List of all configured MAAS instances and their URLs."""

    def _safe_version(c: MaasRestClient) -> str:
        try:
            return c.get_version()
        except Exception:
            return "unknown"

    data = {
        name: {"url": client.base_url, "version": _safe_version(client)}
        for name, client in maas_clients.items()
    }
    return json.dumps(data, indent=2)


@mcp.resource("maas://config")
def resource_config() -> str:
    """MAAS configuration summary: instances, versions, and key settings."""
    data: dict[str, Any] = {
        "instances": {},
    }
    for name, client in maas_clients.items():
        entry: dict[str, Any] = {"url": client.base_url}
        try:
            entry["version"] = client.get_version()
            for key in ("maas_name", "default_osystem", "default_distro_series"):
                try:
                    entry[key] = client.get("maas", params={"op": "get_config", "name": key})
                except Exception:
                    pass
        except Exception:
            entry["version"] = "unreachable"
        data["instances"][name] = entry
    return json.dumps(data, indent=2)


# ===== Resource Templates =====


@mcp.resource("maas://{instance}/machines")
def resource_machines(instance: str) -> str:
    """List machines in a MAAS instance as a browsable resource.

    Returns hostname, system_id, status, and power_state for each machine.
    """
    client = get_client(instance)
    machines = _normalize_list_response(client.get("machines"))
    summary = [
        {
            "hostname": m.get("hostname"),
            "system_id": m.get("system_id"),
            "status_name": m.get("status_name"),
            "power_state": m.get("power_state"),
        }
        for m in machines
    ]
    return json.dumps(summary, indent=2)


@mcp.resource("maas://{instance}/machine/{system_id}")
def resource_machine_detail(instance: str, system_id: str) -> str:
    """Detailed view of a single MAAS machine as a resource."""
    client = get_client(instance)
    machine = client.get(f"machines/{system_id}")
    return json.dumps(_safe_dict(machine), indent=2)


@mcp.resource("maas://{instance}/events")
def resource_events(instance: str) -> str:
    """Recent events from a MAAS instance (last 50)."""
    client = get_client(instance)
    events = client.get("events", params={"op": "query", "limit": 50})
    return json.dumps(_safe_dict(events), indent=2)


# ===== MCP Prompts =====


@mcp.prompt
def investigate_machine(system_id: str = "", hostname: str = "") -> str:
    """Investigate the health and configuration of a MAAS machine."""
    target = f"system_id={system_id}" if system_id else f"hostname={hostname}"
    return (
        f"I need to investigate the health of a MAAS machine ({target}). Please:\n"
        "1. Use maas_get_machine with include=['interfaces','storage','power_parameters','power_state','events'] "
        "to fetch all machine details in one call\n"
        "2. Summarize any anomalies or concerns found"
    )


@mcp.prompt
def audit_drift(baseline_system_id: str = "", target_system_ids: str = "") -> str:
    """Compare configuration between a baseline machine and targets to detect drift."""
    return (
        f"Audit configuration drift: baseline={baseline_system_id}, "
        f"targets={target_system_ids}.\n"
        "Use maas_audit_config with the baseline and target system_ids to compare "
        "NICs, storage, and BIOS. Report any differences found."
    )


@mcp.prompt
def sync_bmc_credentials(system_id: str = "") -> str:
    """Sync BMC credentials between MAAS and the actual BMC via Redfish."""
    return (
        f"Sync BMC credentials for machine {system_id}:\n"
        "1. Use maas_get_machine with include=['power_parameters'] to read current MAAS power config\n"
        "2. Use maas_list_bmc_accounts_redfish to verify the BMC account exists\n"
        "3. Use maas_set_bmc_account_password_from_maas with sync_back_to_maas=true "
        "to update the password on both BMC and MAAS\n"
        "4. Confirm the login was verified successfully"
    )


# ===== System & Discovery Tools =====


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_status(ctx: Context, instance: InstanceParam = "default") -> dict[str, Any]:
    """Check connectivity, get version, and list all configured MAAS instances.

    Returns version info for the target instance plus a summary of all
    configured instances with their URLs and versions.
    """
    await ctx.info(f"Checking MAAS status for instance '{instance}'")
    target_client = get_client(instance)
    version_info = _ensure_json_serializable(target_client.get("version"))

    instances_summary = {}
    for name, client in maas_clients.items():
        try:
            instances_summary[name] = {"url": client.base_url, "version": client.get_version()}
        except Exception:
            instances_summary[name] = {"url": client.base_url, "version": "unreachable"}
            await ctx.warning(f"Instance '{name}' is unreachable")

    return {
        "instance": instance,
        "version": version_info,
        "all_instances": instances_summary,
    }


# ===== Machine Tools =====


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_machines(
    ctx: Context,
    instance: InstanceParam = "default",
    filters: dict[str, Any] | None = None,
    fields: FieldsParam = None,
    allocated_only: Annotated[
        bool,
        Field(default=False, description="Only list machines allocated to the current user"),
    ] = False,
) -> list[dict[str, Any]]:
    """List machines visible to the current user.

    NOTE: MAAS uses vendor hostnames (e.g. gpu001), not NetBox names.
    Map via NetBox ``custom_fields.Provider_Machine_ID``.

    Common fields: system_id, hostname, status_name, power_state, zone, pool,
    tag_names, cpu_count, memory, ip_addresses, interface_set, blockdevice_set.

    Supported filters (passed as query params to MAAS API):
      hostname  - exact match; pass a list for batch lookup
                  e.g. {"hostname": ["gpu037", "gpu063", "gpu081"]}
      status    - Lowercase string aliases (``ready``, ``deployed``) per MAAS.
                  You may also pass integers or numeric strings — they are
                  coerced to lowercase string aliases before calling MAAS.
                  See tool ``maas_node_status_values`` for the full map.
      zone      - availability zone name
      pool      - resource pool name
      tags      - tag name (note: MAAS API uses ``tags`` plural)

    Pagination: do not assume ``limit=`` works on all MAAS versions; some
    controllers reject unknown query constraints. Prefer narrowing with
    ``hostname``, ``status``, or ``tag`` filters.

    The response includes interface_set (check for bond interfaces) and
    blockdevice_set (disk inventory) per machine.

    Use allocated_only=True to see only machines allocated to the current user.
    Use maas_status to list available instances.
    """
    client = get_client(instance)
    params = apply_status_coercion_to_machine_params(dict(filters or {}))
    if "tag" in params:
        params["tags"] = params.pop("tag")
    if allocated_only:
        params["op"] = "list_allocated"
    await ctx.info(
        f"Listing machines on '{instance}'" + (f" with filters={params}" if params else "")
    )
    result = _normalize_list_response(client.get("machines", params=params))
    await ctx.debug(f"Found {len(result)} machines")
    if fields:
        result = _select_fields(result, fields)
    return _safe_list(result)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_node_status_values(ctx: Context) -> list[dict[str, Any]]:
    """Reference: MAAS NodeStatus numeric codes for ``maas_list_machines`` filters.

    Use these ``value`` integers (or the lowercase ``keys``) for the ``status``
    filter on ``GET /api/2.0/machines/``. ``status_name`` matches MAAS
    ``status_name`` on machine objects.

    Source: ``maascommon.enums.node.NodeStatus`` in upstream MAAS.
    """
    await ctx.info("Returning NodeStatus reference (read-only)")
    return list(NODE_STATUS_REFERENCE)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_get_machine(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    include: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Extra sections to fetch in one call. "
                "Options: interfaces, storage, power_parameters, power_state, events, "
                "details, volume_groups, raids. "
                "Example: ['interfaces', 'storage']"
            ),
        ),
    ] = None,
    include_secrets: bool = False,
    fields: FieldsParam = None,
) -> dict[str, Any]:
    """Get full machine details by system_id (preferred) or machine_id.

    NOTE: MAAS uses vendor hostnames (e.g. gpu001), not NetBox names.
    Map via NetBox ``custom_fields.Provider_Machine_ID``.

    Use the 'include' parameter to fetch related data in a single call
    instead of making separate requests for interfaces, storage,
    power parameters, power state, or recent events.
    """
    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    await ctx.info(f"Fetching machine {sid} from '{instance}'")
    machine = client.get(f"machines/{sid}")

    sections = {s.strip().lower() for s in (include or [])}

    if "interfaces" in sections:
        await ctx.debug(f"Fetching interfaces for {sid}")
        machine["interfaces"] = _normalize_list_response(client.get(f"nodes/{sid}/interfaces"))

    if "storage" in sections:
        await ctx.debug(f"Fetching storage for {sid}")
        machine["block_devices"] = _normalize_list_response(client.get(f"nodes/{sid}/blockdevices"))

    if "power_parameters" in sections:
        await ctx.debug(f"Fetching power parameters for {sid}")
        power = client.get(f"machines/{sid}", params={"op": "power_parameters"})
        if isinstance(power, dict) and not include_secrets and "power_pass" in power:
            power = dict(power)
            power["power_pass"] = "***REDACTED***"
        machine["power_parameters"] = power

    if "power_state" in sections:
        await ctx.debug(f"Querying power state for {sid}")
        machine["power_state_queried"] = client.get(
            f"machines/{sid}", params={"op": "query_power_state"}
        )

    if "details" in sections:
        await ctx.debug(f"Fetching hardware details (lshw/lldp) for {sid}")
        machine["details"] = client.get_safe(f"machines/{sid}", params={"op": "details"})

    if "volume_groups" in sections:
        await ctx.debug(f"Fetching volume groups for {sid}")
        machine["volume_groups"] = _normalize_list_response(
            client.get(f"nodes/{sid}/volume-groups")
        )

    if "raids" in sections:
        await ctx.debug(f"Fetching RAID arrays for {sid}")
        machine["raids"] = _normalize_list_response(client.get(f"nodes/{sid}/raids"))

    if "events" in sections:
        hostname = machine.get("hostname")
        if hostname:
            await ctx.debug(f"Fetching events for {hostname}")
            machine["recent_events"] = client.get(
                "events", params={"op": "query", "limit": 50, "hostname": hostname}
            )

    if fields:
        machine = _select_fields(machine, fields)
    return _safe_dict(machine)


@mcp.tool(
    annotations={"readOnlyHint": False, "idempotentHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "updated_keys": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ok", "system_id", "updated_keys"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_set_machine_power_parameters(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    power_address: str | None = None,
    power_user: str | None = None,
    power_pass: str | None = None,
    skip_check: bool = True,
    allow_write: bool = False,
) -> ToolResult:
    """Update a machine's power parameters in MAAS.

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to update power parameters.")

    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)

    cancelled = await _confirm_or_proceed(ctx, f"Confirm: update power parameters for {sid}?")
    if cancelled:
        return cancelled

    await ctx.info(f"Updating power parameters for {sid}")
    result = _set_machine_power_parameters_impl(
        client,
        system_id=sid,
        power_address=power_address,
        power_user=power_user,
        power_pass=power_pass,
        skip_check=skip_check,
    )
    await ctx.info(f"Power parameters updated for {sid}: {result['updated_keys']}")
    return _tool_result(result)


def _ensure_ipmi_account_type(
    bmc_host: str,
    account_odata_id: str,
    current_types: list[str] | None,
    admin_user: str,
    admin_password: str,
    *,
    etag: str | None = None,
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Try to add "IPMI" to AccountTypes if missing.

    Supermicro GPU nodes (AS-8125GS) support both IPMI+Redfish AccountTypes.
    CPU nodes (SYS-121H-TNR) don't accept IPMI as a value but IPMI still works.
    We attempt the PATCH and silently accept 400 errors.
    """
    if current_types is not None and "IPMI" in current_types:
        return {"ipmi_account_type": "already_present", "account_types": current_types}

    desired = sorted(set((current_types or []) + ["IPMI", "Redfish"]))
    resp = patch_account(
        bmc_host,
        account_odata_id,
        admin_user,
        admin_password,
        {"AccountTypes": desired},
        etag=etag,
        timeout_s=timeout_s,
    )

    if resp.status_code in (200, 204):
        return {"ipmi_account_type": "added", "account_types": desired}

    return {
        "ipmi_account_type": "unsupported",
        "account_types": current_types,
        "patch_status": resp.status_code,
        "note": (
            "BMC does not accept IPMI AccountType "
            "(common on CPU-only Supermicro nodes). IPMI may still work."
        ),
    }


def _get_lockout_info(
    bmc_host: str, admin_user: str, admin_password: str, *, timeout_s: int = 20
) -> dict[str, Any]:
    """Read lockout threshold/duration from AccountService. Best-effort."""
    try:
        svc = get_account_service_info(bmc_host, admin_user, admin_password, timeout_s=timeout_s)
        threshold = svc.get("AccountLockoutThreshold")
        duration = svc.get("AccountLockoutDuration")
        info: dict[str, Any] = {
            "lockout_threshold": threshold,
            "lockout_duration_s": duration,
        }
        if isinstance(threshold, int) and 0 < threshold <= 3:
            info["warning"] = (
                f"Aggressive lockout: threshold={threshold}. "
                "MAAS polling may trigger account lockout."
            )
        return info
    except Exception as e:
        return {"lockout_threshold": None, "error": str(e)}


_ROLE_TO_PRIVILEGE: dict[str, str] = {
    "Administrator": "ADMINISTRATOR",
    "Operator": "OPERATOR",
    "ReadOnly": "USER",
}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "bmc_host": {"type": "string"},
            "bmc_user": {"type": "string"},
            "redfish_login_verified": {"type": "boolean"},
            "maas_synced": {"type": "boolean"},
        },
        "required": ["ok", "system_id", "bmc_host", "bmc_user"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_set_bmc_account_password_from_maas(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    bmc_account_username: str | None = None,
    new_password: str | None = None,
    redfish_admin_user: str | None = None,
    redfish_admin_password: str | None = None,
    sync_back_to_maas: bool = True,
    skip_power_check: bool = True,
    allow_write: bool = False,
) -> ToolResult:
    """Set a BMC (Redfish) account password using MAAS power parameters.

    Flow:
    1) Read MAAS power parameters to learn the BMC address and default power_user.
    2) PATCH Redfish AccountService password for that user.
    3) Ensure the account has IPMI AccountType (needed for MAAS IPMI power commands).
    4) Check account lockout threshold and warn if aggressive.
    5) Verify login with the new credentials (best-effort).
    6) Optionally update MAAS power parameters to match (sync_back_to_maas=true).

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to update BMC/MAAS credentials.")
    if not new_password:
        raise ValueError("new_password is required")
    if not redfish_admin_user or not redfish_admin_password:
        raise ValueError("redfish_admin_user and redfish_admin_password are required")

    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)

    cancelled = await _confirm_or_proceed(
        ctx, f"Confirm: set BMC password and sync credentials for {sid}?"
    )
    if cancelled:
        return cancelled

    await ctx.info(f"Starting BMC credential sync for {sid}")

    power = client.get(f"machines/{sid}", params={"op": "power_parameters"})
    if not isinstance(power, dict):
        raise RuntimeError("Unexpected MAAS power_parameters response")

    bmc_host = power.get("power_address")
    default_user = power.get("power_user")
    if not bmc_host:
        raise RuntimeError("MAAS power parameters did not include power_address")

    target_user = bmc_account_username or default_user
    if not target_user:
        raise RuntimeError("No target user provided and MAAS did not return power_user")

    await ctx.info(f"Setting password for '{target_user}' on BMC {bmc_host}")
    try:
        acct = find_account(
            bmc_host, redfish_admin_user, redfish_admin_password, target_user, timeout_s=20
        )
        set_account_password(
            acct,
            admin_user=redfish_admin_user,
            admin_password=redfish_admin_password,
            new_password=new_password,
            timeout_s=20,
        )
    except RedfishError as e:
        await ctx.error(f"Redfish password set failed: {e}")
        raise ToolError(str(e)) from e

    await ctx.info("Password set on BMC, checking role and IPMI account type")
    role_result: dict[str, Any] = {}
    ipmi_result: dict[str, Any] = {}
    try:
        detail = get_account_detail(
            bmc_host, redfish_admin_user, redfish_admin_password, target_user, timeout_s=20
        )
        current_role = detail.get("RoleId", "")
        if current_role != "Administrator":
            await ctx.warning(f"BMC RoleId is '{current_role}', upgrading to Administrator")
            role_resp = patch_account(
                bmc_host,
                detail["_odata_id"],
                redfish_admin_user,
                redfish_admin_password,
                {"RoleId": "Administrator"},
                etag=detail.get("_etag"),
                timeout_s=20,
            )
            if role_resp.status_code == 200:
                role_result = {"role_upgraded": True, "from": current_role, "to": "Administrator"}
                await ctx.info("BMC RoleId upgraded to Administrator")
            else:
                role_result = {"role_upgraded": False, "error": f"HTTP {role_resp.status_code}"}
                await ctx.warning(f"Failed to upgrade RoleId: HTTP {role_resp.status_code}")
            detail = get_account_detail(
                bmc_host, redfish_admin_user, redfish_admin_password, target_user, timeout_s=20
            )
        else:
            role_result = {"role_upgraded": False, "already": "Administrator"}

        ipmi_result = _ensure_ipmi_account_type(
            bmc_host,
            detail["_odata_id"],
            detail.get("AccountTypes"),
            redfish_admin_user,
            redfish_admin_password,
            etag=detail.get("_etag"),
            timeout_s=20,
        )
    except Exception as e:
        ipmi_result = {"ipmi_account_type": "check_failed", "error": str(e)}
        await ctx.warning(f"IPMI account type check failed: {e}")

    lockout = _get_lockout_info(bmc_host, redfish_admin_user, redfish_admin_password)
    if lockout.get("warning"):
        await ctx.warning(lockout["warning"])

    await ctx.info("Verifying login with new credentials")
    ok_login = verify_login(bmc_host, target_user, new_password, timeout_s=20)

    if sync_back_to_maas:
        await ctx.info(f"Syncing credentials back to MAAS for {sid}")
        _set_machine_power_parameters_impl(
            client,
            system_id=sid,
            power_address=bmc_host,
            power_user=target_user,
            power_pass=new_password,
            skip_check=skip_power_check,
        )

    await ctx.info(
        f"BMC credential sync complete for {sid}: "
        f"login_verified={ok_login}, maas_synced={sync_back_to_maas}"
    )

    return _tool_result(
        {
            "ok": True,
            "system_id": sid,
            "bmc_host": bmc_host,
            "bmc_user": target_user,
            "redfish_login_verified": ok_login,
            "maas_synced": sync_back_to_maas,
            "role": role_result,
            "ipmi_account_type": ipmi_result,
            "lockout": lockout,
        }
    )


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_events(
    ctx: Context,
    instance: InstanceParam = "default",
    hostname: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query MAAS event stream (read-only). Uses GET /events/?op=query."""
    client = get_client(instance)
    await ctx.info("Querying events" + (f" for {hostname}" if hostname else ""))
    params: dict[str, Any] = {"op": "query", "limit": limit}
    if hostname:
        params["hostname"] = hostname
    return _safe_dict(client.get("events", params=params))


async def _pre_deploy_sync_netbox_ip(ctx: Context, client: MaasRestClient, sid: str) -> None:
    """Look up NetBox IP and assign it as static on the bond or boot interface.

    Called from ``maas_run_machine_op`` when ``sync_netbox_ip=True``.
    """
    nb = _get_netbox()
    machine = client.get(f"machines/{sid}")
    hostname = machine.get("hostname", "")
    nb_device = nb.lookup_device(hostname)
    if not nb_device:
        await ctx.warning(f"NetBox device not found for '{hostname}' -- skipping IP sync")
        return

    target_ip = extract_ip(nb_device)
    if not target_ip:
        await ctx.warning(f"No primary_ip4 in NetBox for '{hostname}' -- skipping IP sync")
        return

    iface_set = machine.get("interface_set") or []

    # Prefer bond interface; fall back to boot interface
    target_iface = None
    for iface in iface_set:
        if iface.get("type") == "bond":
            target_iface = iface
            break
    if target_iface is None:
        boot = machine.get("boot_interface") or {}
        boot_id = boot.get("id")
        if boot_id:
            for iface in iface_set:
                if iface.get("id") == boot_id:
                    target_iface = iface
                    break
        if target_iface is None and iface_set:
            target_iface = iface_set[0]

    if not target_iface:
        await ctx.warning("No suitable interface found for IP sync")
        return

    iface_id = target_iface["id"]

    # Find the subnet to link to
    subnet_id: int | None = None
    for link in target_iface.get("links") or []:
        s = link.get("subnet")
        if s:
            subnet_id = s.get("id")
            break
    if subnet_id is None:
        vlan_subnets = _normalize_list_response(client.get("subnets"))
        for s in vlan_subnets:
            cidr = s.get("cidr", "")
            if target_ip.startswith(cidr.rsplit("/", 1)[0].rsplit(".", 1)[0]):
                subnet_id = s.get("id")
                break

    if subnet_id is None:
        await ctx.warning(f"Cannot determine subnet for {target_ip} -- skipping IP sync")
        return

    link_data = {
        "mode": "static",
        "subnet": str(subnet_id),
        "ip_address": target_ip,
        "force": "true",
    }
    client.post(
        f"nodes/{sid}/interfaces/{iface_id}",
        data=link_data,
        params={"op": "link_subnet"},
    )
    await ctx.info(f"Pre-deploy: set {target_iface.get('name')} -> {target_ip} (static)")


async def _power_cycle(
    ctx: Context,
    client: MaasRestClient,
    sid: str,
    *,
    wait: bool,
    wait_timeout_s: int = 600,
    poll_interval_s: int = 10,
) -> ToolResult:
    """Synthesize power_cycle as power_off → poll until off → power_on.

    MAAS has no native ``power_cycle`` endpoint, so we do it in two steps.
    """
    await ctx.info(f"power_cycle: sending power_off to {sid}")
    client.post(f"machines/{sid}", data={}, params={"op": "power_off"})

    # Wait for the machine to actually reach power=off before turning it back on.
    off_timeout = min(wait_timeout_s, 120)
    elapsed = 0
    while elapsed < off_timeout:
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
        machine = client.get(f"machines/{sid}")
        power = machine.get("power_state", "")
        await ctx.debug(f"power_cycle: waiting for off, power={power}, {elapsed}s elapsed")
        if power == "off":
            break
    else:
        return _tool_result(
            {
                "ok": False,
                "system_id": sid,
                "power_state": power,
                "elapsed_s": elapsed,
                "error": "power_cycle: timed out waiting for power_off",
            }
        )

    await ctx.info(f"power_cycle: machine {sid} is off, sending power_on")
    client.post(f"machines/{sid}", data={}, params={"op": "power_on"})

    if not wait:
        return _tool_result({"ok": True, "system_id": sid, "status": "", "power_state": "off"})

    # Poll until the machine comes back on.
    while elapsed < wait_timeout_s:
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
        machine = client.get(f"machines/{sid}")
        power = machine.get("power_state", "")
        status = machine.get("status_name", "")
        await ctx.report_progress(
            progress=elapsed,
            total=wait_timeout_s,
            message=f"power_cycle: {status} (power: {power}, {elapsed}s elapsed)",
        )
        if power == "on":
            await ctx.info(f"power_cycle: machine {sid} is back on")
            return _tool_result(
                {
                    "ok": True,
                    "system_id": sid,
                    "status": status,
                    "power_state": power,
                    "elapsed_s": elapsed,
                }
            )

    return _tool_result(
        {
            "ok": False,
            "system_id": sid,
            "power_state": power,
            "elapsed_s": elapsed,
            "error": "power_cycle: timed out waiting for power_on",
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "status": {"type": "string"},
            "power_state": {"type": "string"},
            "elapsed_s": {"type": "integer"},
            "error": {"type": "string"},
        },
        "required": ["ok", "system_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_run_machine_op(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    op: str | None = None,
    data: dict[str, Any] | None = None,
    allow_write: bool = False,
    wait: bool = False,
    wait_timeout_s: int = 600,
    poll_interval_s: int = 10,
    sync_netbox_ip: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "When true and op='deploy', look up the machine in NetBox and set "
                "the bond (or boot interface) to static mode with the NetBox IP "
                "before deploying. Refuses active cartesia5 devices."
            ),
        ),
    ] = False,
) -> Any:
    """Run an operation (op=...) against /machines/<system_id>/.

    Common ops: power_on, power_off, power_cycle, exit_rescue_mode,
    commission, deploy, release, mark_broken, mark_fixed.

    Set wait=True to poll until the operation reaches a terminal state
    (or times out). Progress notifications are emitted while polling.

    Set sync_netbox_ip=True with op='deploy' to automatically assign
    the NetBox IP to the bond or boot interface before deploying.

    Write operation -- requires allow_write=true.

    If the POST times out (common for power/deploy ops that talk to BMC),
    the operation is assumed accepted and polling begins automatically.

    **Alternative verification via Redfish** (use oob_ip from NetBox):
    Hints are auto-generated from ``redfish_mcp.hints`` — check the
    ``hints`` dict in the response for up-to-date tool call examples.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to run machine operations.")
    if not op:
        raise ValueError("op is required")

    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)

    # Pre-deploy: sync NetBox IP to the bond or boot interface
    if sync_netbox_ip and op == "deploy":
        await _pre_deploy_sync_netbox_ip(ctx, client, sid)

    cancelled = await _confirm_or_proceed(ctx, f"Confirm: run '{op}' on machine {sid}?")
    if cancelled:
        return _tool_result({"ok": False, "system_id": sid, "error": "Operation cancelled by user"})

    # MAAS API has no native power_cycle op; synthesize as power_off + power_on.
    if op == "power_cycle":
        return await _power_cycle(
            ctx,
            client,
            sid,
            wait=wait,
            wait_timeout_s=wait_timeout_s,
            poll_interval_s=poll_interval_s,
        )

    _hints = REDFISH_HINTS.as_agent_hints(host="<oob_ip>")
    _hints["note"] = "Use oob_ip from NetBox (not primary_ip) for Redfish/BMC access."

    # Pre-check: is the machine already in a transitional or target state?
    machine_pre = client.get(f"machines/{sid}")
    pre_status = machine_pre.get("status_name", "")
    pre_power = machine_pre.get("power_state", "")
    states = _OP_STATES.get(op, {})

    if pre_status in states.get("success", []):
        await ctx.info(f"Already in target state: {pre_status}")
        return _tool_result(
            {
                "ok": True,
                "system_id": sid,
                "status": pre_status,
                "power_state": pre_power,
                "already_done": True,
            }
        )
    if pre_power in states.get("success_power", []):
        await ctx.info(f"Already in target power state: {pre_power}")
        return _tool_result(
            {
                "ok": True,
                "system_id": sid,
                "status": pre_status,
                "power_state": pre_power,
                "already_done": True,
            }
        )
    if pre_status in states.get("in_progress", []):
        await ctx.info(
            f"Already in progress: {pre_status}. "
            "Skipping duplicate request — will poll for completion."
        )
        # Skip the POST, jump straight to polling
        result = machine_pre
        timed_out = False
        # Fall through to wait loop below
    else:
        await ctx.info(f"Running op='{op}' on machine {sid}")
        result, timed_out = client.post_fire(
            f"machines/{sid}",
            data=data or {},
            params={"op": op},
        )

    if timed_out:
        await ctx.warning(
            f"MAAS POST timed out for op='{op}' on {sid} — likely accepted. Polling status."
        )

    if not wait:
        return _tool_result(
            {
                "ok": True,
                "system_id": sid,
                "accepted": True,
                "timed_out": timed_out,
                "status": result.get("status_name", "") if isinstance(result, dict) else "",
                "power_state": result.get("power_state", "") if isinstance(result, dict) else "",
                "hints": _hints,
            }
        )

    await ctx.info(f"Waiting for '{op}' to complete (timeout={wait_timeout_s}s)")
    states = _OP_STATES.get(op, {})
    elapsed = 0
    status = ""
    power = ""
    while elapsed < wait_timeout_s:
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
        machine = client.get(f"machines/{sid}")
        status = machine.get("status_name", "")
        power = machine.get("power_state", "")

        await ctx.report_progress(
            progress=elapsed,
            total=wait_timeout_s,
            message=f"{status} (power: {power}, {elapsed}s elapsed)",
        )
        await ctx.debug(f"Poll: status={status}, power={power}, elapsed={elapsed}s")

        if status in states.get("success", []):
            await ctx.info(f"Operation '{op}' succeeded: status={status}")
            return _tool_result(
                {
                    "ok": True,
                    "system_id": sid,
                    "status": status,
                    "power_state": power,
                    "elapsed_s": elapsed,
                }
            )
        if power in states.get("success_power", []):
            await ctx.info(f"Operation '{op}' succeeded: power={power}")
            return _tool_result(
                {
                    "ok": True,
                    "system_id": sid,
                    "status": status,
                    "power_state": power,
                    "elapsed_s": elapsed,
                }
            )
        if status in states.get("failure", []):
            await ctx.error(f"Operation '{op}' failed: {status}")
            return _tool_result(
                {
                    "ok": False,
                    "system_id": sid,
                    "status": status,
                    "power_state": power,
                    "elapsed_s": elapsed,
                    "error": f"Operation failed: {status}",
                }
            )

    in_progress = status in states.get("in_progress", [])
    if in_progress:
        await ctx.info(
            f"Operation '{op}' still in progress after {elapsed}s (status={status}). "
            "This is normal for long operations. Do NOT retry or mark_broken — just poll later."
        )
    else:
        await ctx.warning(f"Operation '{op}' timed out after {elapsed}s (status={status})")

    return _tool_result(
        {
            "ok": in_progress,
            "system_id": sid,
            "status": status,
            "power_state": power,
            "elapsed_s": elapsed,
            "in_progress": in_progress,
            "message": (
                f"Still {status} after {elapsed}s — operation is in progress, do NOT retry or mark_broken. "
                "Poll with maas_list_machines or maas-cli machines to check status."
                if in_progress
                else f"Unexpected state {status} after {elapsed}s"
            ),
            "hints": _hints,
        }
    )


# ---------------------------------------------------------------------------
# Boot image management
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_boot_images(
    ctx: Context,
    instance: InstanceParam = "default",
    fields: FieldsParam = None,
    include_detail: Annotated[
        bool,
        Field(
            default=False,
            description="Include per-file download completion status for each image (slower, fetches each resource individually)",
        ),
    ] = False,
    name_filter: Annotated[
        str | None,
        Field(
            default=None,
            description="Filter images by name substring (e.g. 'nvidia-580'). Only with include_detail=true.",
        ),
    ] = None,
    include_uploaded: Annotated[
        bool,
        Field(
            default=False,
            description="Include Uploaded (custom) images in addition to Synced. Only with include_detail=true.",
        ),
    ] = False,
) -> dict[str, Any]:
    """List boot images, configured selections, and import status.

    By default returns synced images with basic info. Set include_detail=true
    to get per-file download completion status (useful for diagnosing stuck
    image downloads or checking custom image upload progress).

    Returns:
        boot_resources: Images (name, arch, size, type; plus sets/files detail if include_detail).
        boot_selections: Configured OS/release/arch selections that sync daily.
        is_importing: Whether an image import is currently running.
        boot_source_id: The primary boot source ID (pass to maas_manage_boot_images).
    """
    client = get_client(instance)
    await ctx.info("Fetching boot images and selections")

    if include_detail or include_uploaded:
        resources = _normalize_list_response(client.get("boot-resources"))
    else:
        resources = _normalize_list_response(
            client.get("boot-resources", params={"type": "synced"})
        )

    is_importing = client.get("boot-resources", params={"op": "is_importing"})

    sources = _normalize_list_response(client.get("boot-sources"))
    source_id = sources[0]["id"] if sources else None
    selections: list[dict[str, Any]] = []
    if source_id is not None:
        selections = _normalize_list_response(client.get(f"boot-sources/{source_id}/selections"))

    detailed_resources: list[dict[str, Any]] = []
    if include_detail:
        for res in resources:
            rid = res.get("id")
            name = res.get("name", "")
            if name_filter and name_filter.lower() not in name.lower():
                continue
            detail = client.get(f"boot-resources/{rid}")
            if not isinstance(detail, dict):
                continue
            summary: dict[str, Any] = {
                "id": rid,
                "name": name,
                "architecture": detail.get("architecture"),
                "type": detail.get("type"),
                "sets": {},
            }
            for ver, sinfo in detail.get("sets", {}).items():
                files_summary = {}
                for fname, fdata in sinfo.get("files", {}).items():
                    files_summary[fname] = {
                        "size": fdata.get("size"),
                        "complete": fdata.get("complete"),
                    }
                summary["sets"][ver] = {
                    "complete": sinfo.get("complete"),
                    "size": sinfo.get("size"),
                    "files": files_summary,
                }
            detailed_resources.append(summary)
        boot_resources = _safe_list(detailed_resources)
    else:
        boot_resources = _select_fields(_safe_list(resources), fields)

    result: dict[str, Any] = {
        "boot_resources": boot_resources,
        "boot_selections": _safe_list(selections),
        "is_importing": bool(is_importing),
        "boot_source_id": source_id,
    }
    await ctx.info(
        f"Found {len(boot_resources)} images, {len(selections)} selections, "
        f"importing={result['is_importing']}"
    )
    return result


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "action": {"type": "string"},
            "detail": {"type": "string"},
            "error": {"type": "string"},
        },
        "required": ["ok", "action"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_manage_boot_images(
    ctx: Context,
    action: str,
    instance: InstanceParam = "default",
    source_id: int | None = None,
    selection_id: int | None = None,
    os: str | None = None,
    release: str | None = None,
    arches: str = "amd64",
    allow_write: bool = False,
) -> ToolResult:
    """Add or remove boot-image selections, delete images, or trigger/stop imports.

    Actions:
        add_selection    -- Enable syncing of an OS release (requires os, release).
        remove_selection -- Remove a selection by ID (requires selection_id).
        delete           -- Delete a boot image by ID (requires selection_id as image ID).
                            Use to clean up stale/incomplete uploads.
        import           -- Trigger an image sync from configured boot sources.
        stop_import      -- Stop a running import.

    Use maas_list_boot_images(include_detail=true) to discover IDs and completion status.

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to manage boot images.")

    valid_actions = ("add_selection", "remove_selection", "delete", "import", "stop_import")
    if action not in valid_actions:
        raise ValueError(f"action must be one of {valid_actions}, got '{action}'")

    client = get_client(instance)

    if source_id is None and action in ("add_selection", "remove_selection"):
        sources = _normalize_list_response(client.get("boot-sources"))
        if not sources:
            return _tool_result(
                {"ok": False, "action": action, "error": "No boot sources configured"}
            )
        source_id = sources[0]["id"]

    if action == "add_selection":
        if not os or not release:
            raise ValueError("os and release are required for add_selection")
        cancelled = await _confirm_or_proceed(
            ctx, f"Confirm: enable sync for {os}/{release} ({arches})?"
        )
        if cancelled:
            return _tool_result({"ok": False, "action": action, "error": "Cancelled by user"})

        await ctx.info(f"Adding boot selection: {os}/{release} arch={arches}")
        client.post(
            f"boot-sources/{source_id}/selections",
            data={"os": os, "release": release, "arches": arches, "subarches": "*", "labels": "*"},
        )
        return _tool_result(
            {
                "ok": True,
                "action": action,
                "detail": f"Added selection for {os}/{release} ({arches}). Run action='import' to start syncing.",
            }
        )

    if action == "remove_selection":
        if selection_id is None:
            raise ValueError("selection_id is required for remove_selection")
        cancelled = await _confirm_or_proceed(
            ctx, f"Confirm: remove boot selection {selection_id} from source {source_id}?"
        )
        if cancelled:
            return _tool_result({"ok": False, "action": action, "error": "Cancelled by user"})

        await ctx.info(f"Removing boot selection {selection_id}")
        client.delete(f"boot-sources/{source_id}/selections/{selection_id}")
        return _tool_result(
            {
                "ok": True,
                "action": action,
                "detail": f"Removed selection {selection_id} from source {source_id}.",
            }
        )

    if action == "delete":
        if selection_id is None:
            raise ValueError(
                "selection_id is required for delete (use as image ID from maas_list_boot_images)"
            )
        detail = client.get(f"boot-resources/{selection_id}")
        name = detail.get("name", "unknown") if isinstance(detail, dict) else "unknown"
        cancelled = await _confirm_or_proceed(
            ctx, f"Confirm: delete boot image {selection_id} ({name})?"
        )
        if cancelled:
            return _tool_result({"ok": False, "action": action, "error": "Cancelled by user"})

        await ctx.info(f"Deleting boot image {selection_id} ({name})")
        client.delete(f"boot-resources/{selection_id}")
        return _tool_result(
            {
                "ok": True,
                "action": action,
                "detail": f"Deleted boot image {selection_id} ({name}).",
            }
        )

    if action == "import":
        cancelled = await _confirm_or_proceed(ctx, "Confirm: trigger boot image import?")
        if cancelled:
            return _tool_result({"ok": False, "action": action, "error": "Cancelled by user"})

        await ctx.info("Triggering boot image import")
        client.post("boot-resources", params={"op": "import"})
        return _tool_result(
            {
                "ok": True,
                "action": action,
                "detail": "Import started. Use maas_list_boot_images to check is_importing status.",
            }
        )

    # stop_import
    cancelled = await _confirm_or_proceed(ctx, "Confirm: stop running boot image import?")
    if cancelled:
        return _tool_result({"ok": False, "action": action, "error": "Cancelled by user"})

    await ctx.info("Stopping boot image import")
    client.post("boot-resources", params={"op": "stop_import"})
    return _tool_result(
        {
            "ok": True,
            "action": action,
            "detail": "Import stop requested.",
        }
    )


# ---------------------------------------------------------------------------
# Redfish BMC account tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_bmc_accounts_redfish(
    ctx: Context,
    bmc_host: str,
    redfish_admin_user: str,
    redfish_admin_password: str,
) -> list[dict[str, Any]]:
    """List Redfish BMC user accounts (non-secret fields only)."""
    await ctx.info(f"Listing BMC accounts on {bmc_host}")
    try:
        return list_accounts(bmc_host, redfish_admin_user, redfish_admin_password, timeout_s=20)
    except RedfishError as e:
        await ctx.error(f"Failed to list BMC accounts: {e}")
        raise ToolError(str(e)) from e


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "bmc_host": {"type": "string"},
            "username": {"type": "string"},
            "role_id": {"type": "string"},
            "login_verified": {"type": "boolean"},
        },
        "required": ["ok", "bmc_host", "username", "role_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_create_bmc_account_redfish(
    ctx: Context,
    bmc_host: str,
    username: str,
    password: str,
    redfish_admin_user: str,
    redfish_admin_password: str,
    role_id: str = "Administrator",
    allow_write: bool = False,
    verify: bool = True,
) -> ToolResult:
    """Create a Redfish BMC user account.

    After creation, attempts to set AccountTypes to ["IPMI", "Redfish"] so
    the account works for both Redfish and IPMI power commands. Some BMC
    models (e.g. SYS-121H-TNR) don't support IPMI as an AccountType value;
    those 400 errors are caught and reported but don't fail the operation.

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to create BMC accounts.")

    cancelled = await _confirm_or_proceed(
        ctx, f"Confirm: create BMC account '{username}' on {bmc_host}?"
    )
    if cancelled:
        return cancelled

    await ctx.info(f"Creating BMC account '{username}' on {bmc_host} (role={role_id})")
    try:
        create_account(
            bmc_host,
            admin_user=redfish_admin_user,
            admin_password=redfish_admin_password,
            username=username,
            password=password,
            role_id=role_id,
            enabled=True,
            timeout_s=20,
        )
    except RedfishError as e:
        await ctx.error(f"Failed to create BMC account: {e}")
        raise ToolError(str(e)) from e

    ipmi_result: dict[str, Any] = {}
    try:
        detail = get_account_detail(
            bmc_host, redfish_admin_user, redfish_admin_password, username, timeout_s=20
        )
        ipmi_result = _ensure_ipmi_account_type(
            bmc_host,
            detail["_odata_id"],
            detail.get("AccountTypes"),
            redfish_admin_user,
            redfish_admin_password,
            etag=detail.get("_etag"),
            timeout_s=20,
        )
    except Exception as e:
        ipmi_result = {"ipmi_account_type": "check_failed", "error": str(e)}
        await ctx.warning(f"IPMI account type check failed: {e}")

    ok_login = verify_login(bmc_host, username, password, timeout_s=20) if verify else None
    await ctx.info(f"BMC account '{username}' created on {bmc_host}: login_verified={ok_login}")
    return _tool_result(
        {
            "ok": True,
            "bmc_host": bmc_host,
            "username": username,
            "role_id": role_id,
            "login_verified": ok_login,
            "ipmi_account_type": ipmi_result,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "system_id": {"type": "string"},
            "bmc_host": {"type": "string"},
            "power_user": {"type": "string"},
            "account_found": {"type": "boolean"},
            "healthy": {"type": "boolean"},
            "issues": {"type": "array", "items": {"type": "string"}},
            "password_verified": {"type": "boolean"},
        },
        "required": ["system_id", "bmc_host", "power_user"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_check_bmc_health(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    redfish_admin_user: str | None = None,
    redfish_admin_password: str | None = None,
) -> ToolResult:
    """Check BMC account health for a MAAS machine.

    Reads MAAS power parameters, then checks via Redfish:
    - Whether the MAAS power user account exists on the BMC
    - Account lock status
    - Role/privilege alignment between BMC and MAAS
    - Whether IPMI access is enabled (AccountTypes)
    - Account lockout threshold settings
    - Password verification with the stored MAAS credentials

    Uses redfish_admin credentials to query the BMC. Falls back to
    MAAS power_user/power_pass if admin credentials are not provided.
    """
    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    await ctx.info(f"Checking BMC health for machine {sid}")

    power = client.get(f"machines/{sid}", params={"op": "power_parameters"})
    if not isinstance(power, dict):
        raise RuntimeError("Unexpected MAAS power_parameters response")

    bmc_host = power.get("power_address")
    power_user = power.get("power_user")
    power_pass = power.get("power_pass")
    maas_privilege = power.get("privilege_level", "")

    if not bmc_host:
        raise RuntimeError("MAAS power parameters did not include power_address")
    if not power_user:
        raise RuntimeError("MAAS power parameters did not include power_user")

    admin_user = redfish_admin_user or power_user
    admin_pass = redfish_admin_password or power_pass
    if not admin_pass:
        raise RuntimeError(
            "No Redfish credentials available (provide admin creds or ensure power_pass is set)"
        )

    issues: list[str] = []
    report: dict[str, Any] = {
        "system_id": sid,
        "bmc_host": bmc_host,
        "power_user": power_user,
        "maas_privilege_level": maas_privilege,
    }

    try:
        detail = get_account_detail(bmc_host, admin_user, admin_pass, power_user, timeout_s=20)
    except RedfishError:
        issues.append(f"Account '{power_user}' not found on BMC")
        report["account_found"] = False
        report["issues"] = issues
        await ctx.warning(f"BMC account '{power_user}' not found on {bmc_host}")
        return _tool_result(_safe_dict(report))

    report["account_found"] = True
    report["bmc_role_id"] = detail.get("RoleId")
    report["bmc_enabled"] = detail.get("Enabled")
    report["bmc_locked"] = detail.get("Locked")
    report["bmc_account_types"] = detail.get("AccountTypes")

    if detail.get("Locked"):
        issues.append("Account is LOCKED on the BMC")
        await ctx.warning(f"Account '{power_user}' is LOCKED on BMC {bmc_host}")

    if not detail.get("Enabled"):
        issues.append("Account is DISABLED on the BMC")
        await ctx.warning(f"Account '{power_user}' is DISABLED on BMC {bmc_host}")

    bmc_role = detail.get("RoleId", "")
    if maas_privilege and bmc_role:
        expected_privilege = _ROLE_TO_PRIVILEGE.get(bmc_role, bmc_role.upper())
        if maas_privilege.upper() != expected_privilege:
            issues.append(
                f"Role mismatch: BMC RoleId={bmc_role} (implies {expected_privilege}) "
                f"but MAAS privilege_level={maas_privilege}"
            )
    if bmc_role and bmc_role != "Administrator":
        issues.append(
            f"BMC RoleId is '{bmc_role}', not 'Administrator'. "
            "MAAS commissioning may have reset this."
        )

    account_types = detail.get("AccountTypes") or []
    if account_types and "IPMI" not in account_types:
        issues.append(
            f"AccountTypes={account_types} — missing 'IPMI'. "
            "On some Supermicro CPU nodes (e.g. SYS-121H-TNR) this is cosmetic and IPMI still works. "
            "If MAAS power control fails, check for DB contention (500 errors) before suspecting BMC issues."
        )

    lockout = _get_lockout_info(bmc_host, admin_user, admin_pass)
    report["lockout"] = lockout
    if lockout.get("warning"):
        issues.append(lockout["warning"])
        await ctx.warning(lockout["warning"])

    if power_pass:
        ok_login = verify_login(bmc_host, power_user, power_pass, timeout_s=20)
        report["password_verified"] = ok_login
        if not ok_login:
            issues.append(
                "Password verification FAILED — MAAS credentials may be out of sync with BMC"
            )
            await ctx.error(f"Password verification FAILED for '{power_user}' on {bmc_host}")
    else:
        report["password_verified"] = None
        issues.append("No power_pass in MAAS — cannot verify password")

    report["issues"] = issues
    report["healthy"] = len(issues) == 0

    if report["healthy"]:
        await ctx.info(f"BMC health check PASSED for {sid}")
    else:
        await ctx.warning(f"BMC health check found {len(issues)} issue(s) for {sid}")

    return _tool_result(_safe_dict(report))


# ===== Networking Tools =====

_NETWORK_RESOURCE_TYPES = (
    "zones",
    "fabrics",
    "subnets",
    "vlans",
    "dns_resources",
    "domains",
    "spaces",
    "dns_records",
    "static_routes",
)

# Target lifecycle states for bond / static IP changes (incl. migration on Deployed stubs).
_ALLOWED_NETWORK_RECONFIG_STATUSES = ("New", "Ready", "Allocated", "Deployed")


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_network(
    ctx: Context,
    resource_type: Annotated[
        str,
        Field(
            description=(
                "Type of network resource: zones, fabrics, subnets, vlans, dns_resources, "
                "domains, spaces, dns_records, static_routes"
            ),
        ),
    ],
    instance: InstanceParam = "default",
    filters: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="Optional filters (applies to subnets, dns_resources)"),
    ] = None,
    fabric_id: Annotated[
        int | None,
        Field(
            default=None, description="Filter VLANs by fabric ID (only for resource_type='vlans')"
        ),
    ] = None,
) -> list[dict[str, Any]]:
    """List MAAS network resources.

    Supported resource_types: zones, fabrics, subnets, vlans, dns_resources,
    domains, spaces, dns_records, static_routes.

    Use fabric_id to scope VLANs to a single fabric. When omitted with
    resource_type='vlans', VLANs from every fabric are returned.
    """
    rt = resource_type.strip().lower()
    if rt not in _NETWORK_RESOURCE_TYPES:
        raise ValueError(
            f"resource_type must be one of {_NETWORK_RESOURCE_TYPES}, got '{resource_type}'"
        )

    client = get_client(instance)
    await ctx.info(f"Listing {rt} from '{instance}'")

    if rt == "zones":
        return _safe_list(_normalize_list_response(client.get("zones")))

    if rt == "fabrics":
        return _safe_list(_normalize_list_response(client.get("fabrics")))

    if rt == "subnets":
        return _safe_list(_normalize_list_response(client.get("subnets", params=filters or {})))

    if rt == "vlans":
        if fabric_id is not None:
            return _safe_list(_normalize_list_response(client.get(f"fabrics/{fabric_id}/vlans")))
        fabrics = _normalize_list_response(client.get("fabrics"))
        all_vlans: list[Any] = []
        for fabric in fabrics:
            fid = fabric.get("id")
            if fid is None:
                continue
            try:
                all_vlans.extend(_normalize_list_response(client.get(f"fabrics/{fid}/vlans")))
            except Exception:
                continue
        return _safe_list(all_vlans)

    if rt == "dns_resources":
        return _safe_list(
            _normalize_list_response(client.get("dnsresources", params=filters or {}))
        )

    if rt == "domains":
        return _safe_list(_normalize_list_response(client.get("domains")))

    if rt == "spaces":
        return _safe_list(_normalize_list_response(client.get("spaces")))

    if rt == "dns_records":
        return _safe_list(
            _normalize_list_response(client.get("dnsresourcerecords", params=filters or {}))
        )

    if rt == "static_routes":
        return _safe_list(_normalize_list_response(client.get("static-routes")))

    return []


# ===== Rack Controller Tools =====


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_rack_controllers(
    ctx: Context,
    instance: InstanceParam = "default",
    hostname: Annotated[
        str | None,
        Field(default=None, description="Filter by hostname (substring match)"),
    ] = None,
    fields: FieldsParam = None,
    include_power_types: Annotated[
        bool,
        Field(
            default=False, description="Include available power driver types (redfish, ipmi, etc.)"
        ),
    ] = False,
) -> dict[str, Any] | list[dict[str, Any]]:
    """List rack controllers with their service health status.

    Returns each rack controller's hostname, system_id, zone, IP addresses,
    and service_set (rackd, dhcpd, dhcpd6, tftp, dns_rack, ntp_rack,
    proxy_rack, syslog_rack, http, agent status).

    Use include_power_types=True to also list available power drivers.
    Use this to diagnose DHCP, PXE boot, or commissioning failures
    caused by dead or misconfigured rack controllers.
    """
    client = get_client(instance)
    await ctx.info(f"Listing rack controllers on '{instance}'")
    result = _normalize_list_response(client.get("rackcontrollers"))
    if hostname:
        result = [r for r in result if hostname.lower() in r.get("hostname", "").lower()]
    await ctx.debug(f"Found {len(result)} rack controllers")
    if fields:
        result = _select_fields(result, fields)
    if include_power_types:
        power_types = _normalize_list_response(
            client.get("rackcontrollers", params={"op": "describe_power_types"})
        )
        return _safe_dict(
            {"rack_controllers": _safe_list(result), "power_types": _safe_list(power_types)}
        )
    return _safe_list(result)


# ===== VLAN Management Tools =====


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_get_vlan(
    ctx: Context,
    instance: InstanceParam = "default",
    fabric_id: Annotated[int, Field(description="Fabric ID containing the VLAN")] = 0,
    vid: Annotated[int, Field(description="VLAN ID (0 for untagged)")] = 0,
    fields: FieldsParam = None,
) -> dict[str, Any]:
    """Get details for a single VLAN including DHCP status and rack controller assignments.

    Returns the VLAN's id, vid, name, fabric, mtu, dhcp_on, primary_rack,
    secondary_rack, relay_vlan, and space.
    """
    client = get_client(instance)
    await ctx.info(f"Fetching VLAN vid={vid} on fabric {fabric_id}")
    result = client.get(f"fabrics/{fabric_id}/vlans/{vid}")
    if fields:
        result = _select_fields(result, fields)
    return _safe_dict(result)


@mcp.tool(
    annotations={"readOnlyHint": False, "idempotentHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "vlan_id": {"type": "integer"},
            "dhcp_on": {"type": "boolean"},
            "primary_rack": {"type": "string"},
            "secondary_rack": {"type": "string"},
            "updated_fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ok", "vlan_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_update_vlan(
    ctx: Context,
    instance: InstanceParam = "default",
    fabric_id: Annotated[int, Field(description="Fabric ID containing the VLAN")] = 0,
    vid: Annotated[int, Field(description="VLAN ID (0 for untagged)")] = 0,
    dhcp_on: Annotated[bool | None, Field(default=None, description="Enable/disable DHCP")] = None,
    primary_rack: Annotated[
        str | None,
        Field(default=None, description="System ID of the primary rack controller for DHCP"),
    ] = None,
    secondary_rack: Annotated[
        str | None,
        Field(default=None, description="System ID of the secondary rack controller for DHCP"),
    ] = None,
    mtu: Annotated[int | None, Field(default=None, description="MTU for the VLAN")] = None,
    allow_write: bool = False,
) -> ToolResult:
    """Update a VLAN's DHCP settings, rack controller assignments, or MTU.

    Common use case: switch the primary rack controller when the current
    one is dead, or enable DHCP on a VLAN for PXE boot commissioning.

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to update VLAN settings.")

    client = get_client(instance)

    data: dict[str, Any] = {}
    updated: list[str] = []
    if dhcp_on is not None:
        data["dhcp_on"] = str(dhcp_on).lower()
        updated.append("dhcp_on")
    if primary_rack is not None:
        data["primary_rack"] = primary_rack
        updated.append("primary_rack")
    if secondary_rack is not None:
        data["secondary_rack"] = secondary_rack
        updated.append("secondary_rack")
    if mtu is not None:
        data["mtu"] = str(mtu)
        updated.append("mtu")

    if not data:
        raise ValueError(
            "No fields to update. Provide at least one of: dhcp_on, primary_rack, secondary_rack, mtu"
        )

    cancelled = await _confirm_or_proceed(
        ctx, f"Confirm: update VLAN vid={vid} on fabric {fabric_id}? Fields: {updated}"
    )
    if cancelled:
        return cancelled

    await ctx.info(f"Updating VLAN vid={vid} on fabric {fabric_id}: {updated}")
    result = client.put(f"fabrics/{fabric_id}/vlans/{vid}", data=data)

    return _tool_result(
        {
            "ok": True,
            "vlan_id": result.get("id") if isinstance(result, dict) else None,
            "dhcp_on": result.get("dhcp_on") if isinstance(result, dict) else None,
            "primary_rack": result.get("primary_rack") if isinstance(result, dict) else None,
            "secondary_rack": result.get("secondary_rack") if isinstance(result, dict) else None,
            "updated_fields": updated,
        }
    )


# ===== Interface Subnet Linking =====


@mcp.tool(
    annotations={"readOnlyHint": False, "idempotentHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "interface_id": {"type": "integer"},
            "mode": {"type": "string"},
            "subnet_id": {"type": "integer"},
            "ip_address": {"type": "string"},
        },
        "required": ["ok", "system_id", "interface_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_link_interface_subnet(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    interface_id: Annotated[
        int, Field(description="Interface ID (from maas_get_machine with include=['interfaces'])")
    ] = 0,
    subnet_id: Annotated[int, Field(description="Subnet ID to link to")] = 0,
    mode: Annotated[
        str,
        Field(
            default="dhcp",
            description="Link mode: 'dhcp' (auto-assign from pool), 'static' (requires ip_address), 'auto' (auto-assign static), or 'link_up' (no IP)",
        ),
    ] = "dhcp",
    ip_address: Annotated[
        str | None,
        Field(default=None, description="IP address (required for mode='static')"),
    ] = None,
    force: Annotated[
        bool,
        Field(
            default=False,
            description="Force link even if interface already has a link on this subnet",
        ),
    ] = False,
    default_gateway: Annotated[
        bool,
        Field(
            default=False, description="Set this subnet's gateway as the machine's default gateway"
        ),
    ] = False,
    allow_write: bool = False,
) -> ToolResult:
    """Link a machine's network interface to a subnet.

    This is required before commissioning if MAAS reports "Node has no
    address family in common with the server". The machine's boot
    interface must be linked to the same subnet as the rack controller.

    The machine must be in New, Ready, Allocated, or Broken state.

    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to link interface to subnet.")

    valid_modes = ("dhcp", "static", "auto", "link_up")
    if mode not in valid_modes:
        raise ValueError(f"mode must be one of {valid_modes}, got '{mode}'")
    if mode == "static" and not ip_address:
        raise ValueError("ip_address is required when mode='static'")
    if not interface_id:
        raise ValueError(
            "interface_id is required (use maas_get_machine with include=['interfaces'] to find it)"
        )
    if not subnet_id:
        raise ValueError(
            "subnet_id is required (use maas_list_network resource_type='subnets' to find it)"
        )

    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)

    cancelled = await _confirm_or_proceed(
        ctx, f"Confirm: link interface {interface_id} on {sid} to subnet {subnet_id} (mode={mode})?"
    )
    if cancelled:
        return cancelled

    data: dict[str, Any] = {
        "mode": mode.upper() if mode == "link_up" else mode,
        "subnet": str(subnet_id),
    }
    if ip_address:
        data["ip_address"] = ip_address
    if force:
        data["force"] = "true"
    if default_gateway:
        data["default_gateway"] = "true"

    await ctx.info(f"Linking interface {interface_id} on {sid} to subnet {subnet_id} (mode={mode})")
    result = client.post(
        f"nodes/{sid}/interfaces/{interface_id}", data=data, params={"op": "link_subnet"}
    )

    links = result.get("links", []) if isinstance(result, dict) else []
    assigned_ip = None
    for link in links:
        sub = link.get("subnet", {})
        if isinstance(sub, dict) and sub.get("id") == subnet_id:
            assigned_ip = link.get("ip_address")
            break

    await ctx.info(f"Interface linked: ip={assigned_ip}")
    return _tool_result(
        {
            "ok": True,
            "system_id": sid,
            "interface_id": interface_id,
            "mode": mode,
            "subnet_id": subnet_id,
            "ip_address": assigned_ip,
        }
    )


# ===== IP Range Management =====


@mcp.tool(
    annotations={"readOnlyHint": False, "idempotentHint": False},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "action": {"type": "string"},
            "ranges": {"type": "array", "items": {"type": "object"}},
            "id": {"type": "integer"},
        },
        "required": ["ok", "action"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_ip_ranges(
    ctx: Context,
    action: Annotated[
        str,
        Field(description="Action: 'list', 'create', or 'delete'"),
    ] = "list",
    instance: InstanceParam = "default",
    subnet_id: Annotated[
        int | None,
        Field(
            default=None, description="Subnet ID (required for create, optional filter for list)"
        ),
    ] = None,
    range_id: Annotated[
        int | None,
        Field(default=None, description="Range ID (required for delete)"),
    ] = None,
    range_type: Annotated[
        str,
        Field(
            default="reserved",
            description="Range type: 'reserved' (block from DHCP, allow static) or 'dynamic' (DHCP pool)",
        ),
    ] = "reserved",
    start_ip: Annotated[
        str | None,
        Field(default=None, description="Start IP address (required for create)"),
    ] = None,
    end_ip: Annotated[
        str | None,
        Field(default=None, description="End IP address (required for create)"),
    ] = None,
    comment: Annotated[
        str,
        Field(default="", description="Description/comment for the range"),
    ] = "",
    allow_write: bool = False,
) -> ToolResult:
    """List, create, or delete IP ranges on MAAS subnets.

    Actions:
        list   -- List all IP ranges, optionally filtered by subnet_id.
        create -- Create a reserved or dynamic range (requires subnet_id, start_ip, end_ip).
        delete -- Delete a range by ID (requires range_id).

    Reserved ranges prevent DHCP from assigning addresses in that block
    while keeping them available for static IP assignment via
    maas_link_interface_subnet(mode='static').

    Common use case: reserve a block like 172.20.1.1-254 for static GPU
    node IPs so DHCP only assigns from the rest of the /22.

    Write operations require allow_write=true.
    """
    valid_actions = ("list", "create", "delete")
    if action not in valid_actions:
        raise ValueError(f"action must be one of {valid_actions}, got '{action}'")

    client = get_client(instance)

    if action == "list":
        await ctx.info("Listing IP ranges")
        result = _normalize_list_response(client.get("ipranges"))
        if subnet_id is not None:
            result = [
                r
                for r in result
                if (isinstance(r.get("subnet"), dict) and r["subnet"].get("id") == subnet_id)
                or r.get("subnet") == subnet_id
            ]
        return _tool_result({"ok": True, "action": "list", "ranges": _safe_list(result)})

    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true to create/delete IP ranges.")

    if action == "create":
        if not start_ip or not end_ip or subnet_id is None:
            raise ValueError("start_ip, end_ip, and subnet_id are required for create")

        valid_types = ("reserved", "dynamic")
        if range_type not in valid_types:
            raise ValueError(f"range_type must be one of {valid_types}, got '{range_type}'")

        cancelled = await _confirm_or_proceed(
            ctx, f"Confirm: create {range_type} IP range {start_ip}-{end_ip} on subnet {subnet_id}?"
        )
        if cancelled:
            return cancelled

        await ctx.info(f"Creating {range_type} range {start_ip}-{end_ip} on subnet {subnet_id}")
        result = client.post(
            "ipranges",
            data={
                "type": range_type,
                "start_ip": start_ip,
                "end_ip": end_ip,
                "subnet": str(subnet_id),
                "comment": comment,
            },
        )
        return _tool_result(
            {
                "ok": True,
                "action": "create",
                "id": result.get("id") if isinstance(result, dict) else None,
                "start_ip": start_ip,
                "end_ip": end_ip,
                "type": range_type,
                "comment": comment,
            }
        )

    # delete
    if range_id is None:
        raise ValueError("range_id is required for delete")

    cancelled = await _confirm_or_proceed(ctx, f"Confirm: delete IP range {range_id}?")
    if cancelled:
        return cancelled

    await ctx.info(f"Deleting IP range {range_id}")
    client.delete(f"ipranges/{range_id}")
    return _tool_result({"ok": True, "action": "delete", "id": range_id})


# ===== Config Drift Audit Tools =====

_AUDIT_ASPECTS = ("nics", "storage", "bios")


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_audit_config(
    ctx: Context,
    instance: InstanceParam = "default",
    machine_ids: list[int] | None = None,
    system_ids: list[str] | None = None,
    baseline_machine_id: int | None = None,
    baseline_system_id: str | None = None,
    aspects: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="Config aspects to compare: nics, storage, bios. Defaults to all.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Compare machine configurations to detect drift.

    Compares one or more target machines against a baseline, checking
    the requested aspects (nics, storage, bios). Defaults to all three.
    Accepts multiple targets via machine_ids/system_ids lists.
    """
    selected = {a.strip().lower() for a in (aspects or list(_AUDIT_ASPECTS))}
    invalid = selected - set(_AUDIT_ASPECTS)
    if invalid:
        raise ValueError(f"Invalid aspects: {invalid}. Must be from {_AUDIT_ASPECTS}")

    client = get_client(instance)

    baseline, bl_sid, target_ids = _resolve_audit_targets(
        client,
        machine_ids=machine_ids,
        system_ids=system_ids,
        baseline_machine_id=baseline_machine_id,
        baseline_system_id=baseline_system_id,
    )

    await ctx.info(
        f"Auditing config drift: baseline={bl_sid}, "
        f"targets={target_ids}, aspects={sorted(selected)}"
    )

    if "nics" in selected:
        baseline["interfaces"] = _normalize_list_response(client.get(f"nodes/{bl_sid}/interfaces"))
    if "storage" in selected:
        baseline["block_devices"] = _normalize_list_response(
            client.get(f"nodes/{bl_sid}/blockdevices")
        )

    results: dict[str, Any] = {}
    for tid in target_ids:
        target = client.get(f"machines/{tid}")
        comparison: dict[str, Any] = {}

        if "nics" in selected:
            target["interfaces"] = _normalize_list_response(client.get(f"nodes/{tid}/interfaces"))
            comparison["nics"] = compare_nics(baseline, target)
        if "storage" in selected:
            target["block_devices"] = _normalize_list_response(
                client.get(f"nodes/{tid}/blockdevices")
            )
            comparison["storage"] = compare_storage(baseline, target)
        if "bios" in selected:
            comparison["bios"] = compare_bios(baseline, target)

        results[tid] = {
            "hostname": target.get("hostname"),
            "system_id": tid,
            "comparison": comparison,
        }
        await ctx.debug(f"Compared {tid}: {list(comparison.keys())}")

    return _safe_dict(
        {
            "baseline": {"hostname": baseline.get("hostname"), "system_id": bl_sid},
            "aspects": sorted(selected),
            "targets": results,
        }
    )


# ---------------------------------------------------------------------------
# NetBox network-config sync tools
# ---------------------------------------------------------------------------


def _find_source_machine(
    source_client: MaasRestClient,
    *,
    source_system_id: str | None,
    hostname: str | None,
    target_iface_set: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Locate a machine on the source MAAS by system_id, hostname, or MAC."""
    if source_system_id:
        return source_client.get(f"machines/{source_system_id}")

    if hostname:
        machines = _normalize_list_response(
            source_client.get("machines", params={"hostname": hostname})
        )
        if machines:
            return machines[0]

    if target_iface_set:
        target_macs = {
            (iface.get("mac_address") or "").lower()
            for iface in target_iface_set
            if iface.get("mac_address")
        }
        if target_macs:
            for m in _normalize_list_response(source_client.get("machines")):
                for iface in m.get("interface_set") or []:
                    if (iface.get("mac_address") or "").lower() in target_macs:
                        return source_client.get(f"machines/{m['system_id']}")

    raise ToolError("Could not locate machine on source MAAS instance.")


def _find_subnet_on_target(client: MaasRestClient, cidr: str) -> dict[str, Any] | None:
    """Find a subnet on the target MAAS by CIDR."""
    subnets = _normalize_list_response(client.get("subnets"))
    for s in subnets:
        if s.get("cidr") == cidr:
            return s
    return None


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_get_network_profile(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
) -> dict[str, Any]:
    """Extract a portable network profile (bonds, MTU, IPs, VLANs) from a machine.

    Works on any MAAS instance. Read from the legacy instance (e.g. "ori")
    to capture the network config that should be replicated on the new instance.

    Returns a dict with bonds, physical_interfaces, gateway, and dns_servers.
    """
    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    await ctx.info(f"Extracting network profile from {sid} on {instance}")
    machine = client.get(f"machines/{sid}")
    return extract_network_profile(machine)


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "bond_created": {"type": "object"},
            "ip_assigned": {"type": "string"},
            "netbox_device": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "string"}},
            "error": {"type": "string"},
        },
        "required": ["ok", "system_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_sync_network_config(
    ctx: Context,
    instance: InstanceParam = "default",
    source_instance: Annotated[
        str,
        Field(description="Source MAAS instance to read network config from (e.g. 'ori')"),
    ] = "ori",
    system_id: str | None = None,
    machine_id: int | None = None,
    source_system_id: Annotated[
        str | None,
        Field(
            description="System ID on the source MAAS (auto-detected by hostname/MAC if omitted)"
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(description="Preview changes without writing (default true)"),
    ] = True,
    allow_write: bool = False,
) -> ToolResult:
    """Replicate bond/MTU/IP config from a source MAAS instance to the target.

    Reads the full network profile (bonds, MTU, bond params) from the source
    MAAS, looks up the authoritative IP in NetBox, then creates bonds and sets
    static IPs on the target MAAS.

    The target machine must be Ready, Allocated, or Deployed.

    Write operation -- requires allow_write=true and dry_run=false.
    """
    if not dry_run and not allow_write:
        raise ToolError("Refusing to write: set allow_write=true and dry_run=false.")

    target_client = get_client(instance)
    source_client = get_client(source_instance)
    nb = _get_netbox()

    sid = _resolve_system_id(target_client, system_id, machine_id)
    target_machine = target_client.get(f"machines/{sid}")
    target_status = target_machine.get("status_name") or ""
    hostname = target_machine.get("hostname") or ""

    if target_status not in _ALLOWED_NETWORK_RECONFIG_STATUSES:
        raise ToolError(
            f"Machine {sid} is in '{target_status}' state. "
            f"Must be one of {', '.join(_ALLOWED_NETWORK_RECONFIG_STATUSES)} to modify network config."
        )

    await ctx.info(f"Syncing network config for {hostname} ({sid})")

    # --- NetBox lookup ---
    target_zone = (target_machine.get("zone") or {}).get("name")
    nb_device = nb.lookup_device_for_site(hostname, maas_zone=target_zone)
    netbox_ip = nb_device.get("primary_ip4_address") if nb_device else None
    netbox_name = (nb_device.get("name") or "") if nb_device else ""

    # --- Source profile ---
    target_iface_set = target_machine.get("interface_set") or []
    source_machine = _find_source_machine(
        source_client,
        source_system_id=source_system_id,
        hostname=hostname,
        target_iface_set=target_iface_set,
    )
    profile = extract_network_profile(source_machine)
    await ctx.info(
        f"Source profile: {len(profile['bonds'])} bonds, "
        f"{len(profile['physical_interfaces'])} physical interfaces"
    )

    # --- Match interfaces by MAC ---
    mac_map = match_interfaces_by_mac(profile, target_iface_set)
    actions: list[str] = []

    # --- Check for existing bonds on target ---
    existing_bonds = {iface["name"] for iface in target_iface_set if iface.get("type") == "bond"}

    bond_created: dict[str, Any] = {}

    for bond in profile.get("bonds") or []:
        bond_name = bond["name"]
        if bond_name in existing_bonds:
            actions.append(f"SKIP bond {bond_name}: already exists on target")
            continue

        parent_ids = []
        missing_parents = []
        for parent_name in bond.get("parents") or []:
            tid = mac_map.get(parent_name)
            if tid:
                parent_ids.append(str(tid))
            else:
                missing_parents.append(parent_name)

        if missing_parents:
            actions.append(f"WARN bond {bond_name}: cannot map parents {missing_parents} by MAC")
            continue

        # Resolve subnet/vlan on target for the bond link
        subnet_cidr = None
        for link in bond.get("links") or []:
            if link.get("subnet_cidr"):
                subnet_cidr = link["subnet_cidr"]
                break

        target_subnet = _find_subnet_on_target(target_client, subnet_cidr) if subnet_cidr else None
        target_vlan_id = (target_subnet.get("vlan") or {}).get("id") if target_subnet else None
        target_subnet_id = target_subnet.get("id") if target_subnet else None

        bond_params = bond.get("params") or {}

        # Validate NetBox IP is in the target subnet; fall back to source IP
        ip_to_assign = netbox_ip
        if ip_to_assign and subnet_cidr:
            try:
                if ipaddress.ip_address(ip_to_assign) not in ipaddress.ip_network(
                    subnet_cidr, strict=False
                ):
                    await ctx.info(
                        f"NetBox IP {ip_to_assign} not in bond subnet {subnet_cidr}; "
                        f"falling back to source IP"
                    )
                    ip_to_assign = None
            except ValueError:
                ip_to_assign = None
        if not ip_to_assign:
            for link in bond.get("links") or []:
                if link.get("ip_address"):
                    ip_to_assign = link["ip_address"]
                    break

        create_data: dict[str, Any] = {
            "name": bond_name,
            "parents": parent_ids,
        }
        for k, v in bond_params.items():
            sv = str(v) if not isinstance(v, str) else v
            create_data[k] = sv.replace("+", "%2B")
        if target_vlan_id is not None:
            create_data["vlan"] = str(target_vlan_id)

        if dry_run:
            actions.append(
                f"WOULD CREATE bond {bond_name} with parents={parent_ids}, "
                f"params={bond_params}, ip={ip_to_assign}"
            )
            bond_created = {"name": bond_name, "dry_run": True, **create_data}
        else:
            cancelled = await _confirm_or_proceed(
                ctx,
                f"Confirm: create bond {bond_name} on {sid} with parents={parent_ids}, ip={ip_to_assign}?",
            )
            if cancelled:
                return cancelled

            await ctx.info(f"Creating bond {bond_name} on {sid}")
            result = target_client.post(
                f"nodes/{sid}/interfaces",
                data=create_data,
                params={"op": "create_bond"},
            )
            bond_id = result.get("id") if isinstance(result, dict) else None
            bond_created = {"name": bond_name, "id": bond_id}
            actions.append(f"CREATED bond {bond_name} (id={bond_id})")

            # Link bond to subnet with static IP
            if bond_id and target_subnet_id and ip_to_assign:
                link_data: dict[str, Any] = {
                    "mode": "static",
                    "subnet": str(target_subnet_id),
                    "ip_address": ip_to_assign,
                }
                target_client.post(
                    f"nodes/{sid}/interfaces/{bond_id}",
                    data=link_data,
                    params={"op": "link_subnet"},
                )
                actions.append(f"LINKED bond {bond_name} -> {ip_to_assign} (subnet {subnet_cidr})")

    return _tool_result(
        {
            "ok": True,
            "system_id": sid,
            "hostname": hostname,
            "bond_created": bond_created,
            "ip_assigned": netbox_ip or "",
            "netbox_device": netbox_name,
            "source_instance": source_instance,
            "dry_run": dry_run,
            "actions": actions,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "hostname": {"type": "string"},
            "system_id": {"type": "string"},
            "source_system_id": {"type": "string"},
            "source_instance": {"type": "string"},
            "target_instance": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "steps": {"type": "object"},
        },
        "required": ["ok", "system_id", "source_system_id", "dry_run"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_migrate_node(
    ctx: Context,
    instance: InstanceParam = "default",
    source_instance: Annotated[
        str,
        Field(description="Legacy/source MAAS instance (e.g. 'ori')"),
    ] = "ori",
    system_id: str | None = None,
    machine_id: int | None = None,
    source_system_id: Annotated[
        str | None,
        Field(description="system_id on source MAAS (auto by hostname/MAC if omitted)"),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(description="Preview all steps without writing (default true)"),
    ] = True,
    allow_write: bool = False,
    sync_interfaces: Annotated[
        bool,
        Field(description="Replicate physical interfaces (MACs + names) from source before bonds"),
    ] = True,
    sync_power: Annotated[
        bool,
        Field(description="Copy power_address/user/pass from source machine"),
    ] = True,
    sync_network: Annotated[
        bool,
        Field(description="Create bonds and assign static IPs (maas_sync_network_config)"),
    ] = True,
    sync_metadata: Annotated[
        bool,
        Field(description="Copy hostname, zone, pool, arch, cpu, memory, OS fields from source"),
    ] = True,
    sync_disks: Annotated[
        bool,
        Field(description="Copy block device records (disks) from source to target"),
    ] = True,
    sync_tags: Annotated[
        bool,
        Field(description="Create tags from source and associate machine on target"),
    ] = True,
    sync_hardware_info: Annotated[
        bool,
        Field(description="Copy hardware_info (system_vendor, cpu_model, etc.) via direct DB"),
    ] = True,
    sync_numa_devices: Annotated[
        bool,
        Field(description="Sync NUMA topology, PCI/USB devices, and interface speed fields via DB"),
    ] = True,
    set_deployed: Annotated[
        bool,
        Field(
            description="After sync, mark machine Deployed with admin owner and create commissioning scriptset"
        ),
    ] = False,
) -> ToolResult:
    """One-shot per-node migration from one MAAS region to another.

    Execution order:
      1. sync_interfaces — delete stale NICs, create physical interfaces with
         source MACs and names (no PXE/commissioning needed).
      2. sync_power — copy IPMI power parameters from source.
      3. sync_metadata — copy hostname, zone, pool, arch, cpu, memory, OS.
         (runs before network so NetBox lookup has the correct zone)
      4. sync_network — create bonds, set MTU, assign static IP from NetBox.
      5. sync_disks — copy block device records + partitions from source.
      6. sync_tags — ensure tags exist on target, associate machine.
      7. sync_hardware_info — copy NodeMetadata (vendor, cpu_model, etc.)
         via direct PostgreSQL access (requires MAAS_{SITE}_DB_URL).
      8. sync_numa_devices — NUMA nodes, PCI/USB devices, interface speed
         fields via direct DB.
      9. set_deployed — transition machine to Deployed state (status=6) with
         admin owner, and create a commissioning ScriptSet so the UI shows
         "Deployed" correctly.

    Each step is independently toggleable.  Does not commission or deploy —
    use ``maas_run_machine_op`` afterward if needed, or set ``set_deployed=true``.

    Write path: set ``dry_run=false`` and ``allow_write=true``.
    """
    if not dry_run and not allow_write:
        raise ToolError("Refusing: use dry_run=true to preview, or allow_write=true to apply.")

    target_client = get_client(instance)
    source_client = get_client(source_instance)
    sid = _resolve_system_id(target_client, system_id, machine_id)
    target_machine = target_client.get(f"machines/{sid}")
    hostname = target_machine.get("hostname") or ""
    target_iface_set = target_machine.get("interface_set") or []

    source_machine = _find_source_machine(
        source_client,
        source_system_id=source_system_id,
        hostname=hostname,
        target_iface_set=target_iface_set,
    )
    src_sid = source_machine.get("system_id") or ""
    if not src_sid:
        raise ToolError("Source machine has no system_id")

    enabled = [
        ("interfaces", sync_interfaces),
        ("power", sync_power),
        ("metadata", sync_metadata),
        ("network", sync_network),
        ("disks", sync_disks),
        ("tags", sync_tags),
        ("hardware_info", sync_hardware_info),
        ("numa_devices", sync_numa_devices),
        ("set_deployed", set_deployed),
    ]
    total_steps = sum(on for _, on in enabled)
    step_num = 0

    await ctx.info(
        f"Migrating {hostname}: target {sid} ({instance}) <- source {src_sid} ({source_instance}) "
        f"({total_steps} steps)"
    )

    steps: dict[str, Any] = {}

    # --- 1. interfaces ---
    if sync_interfaces:
        step_num += 1
        await ctx.info(f"Step {step_num}/{total_steps}: replicate physical interfaces from source")
        db_url_iface = _get_db_url(instance)
        steps["interfaces"] = _migrate_sync_interfaces(
            source_client,
            src_sid,
            target_client,
            sid,
            dry_run=dry_run,
            db_url=db_url_iface,
        )
    else:
        steps["interfaces"] = {"ok": True, "skipped": True, "reason": "sync_interfaces=false"}

    # --- 2. power ---
    if sync_power:
        step_num += 1
        await ctx.info(f"Step {step_num}/{total_steps}: power parameters from source")
        steps["power"] = _migrate_copy_power_from_source(
            source_client, src_sid, target_client, sid, dry_run=dry_run
        )
    else:
        steps["power"] = {"ok": True, "skipped": True, "reason": "sync_power=false"}

    # --- 3. metadata (before network so NetBox lookup has correct zone) ---
    if sync_metadata:
        step_num += 1
        await ctx.info(f"Step {step_num}/{total_steps}: machine metadata + OS from source")
        steps["metadata"] = _migrate_sync_metadata(
            source_client, src_sid, target_client, sid, dry_run=dry_run
        )
    else:
        steps["metadata"] = {"ok": True, "skipped": True, "reason": "sync_metadata=false"}

    # --- 4. network ---
    if sync_network:
        step_num += 1
        await ctx.info(f"Step {step_num}/{total_steps}: network config (bonds + IP)")
        net_res = await maas_sync_network_config(
            ctx,
            instance=instance,
            source_instance=source_instance,
            system_id=sid,
            machine_id=None,
            source_system_id=src_sid,
            dry_run=dry_run,
            allow_write=allow_write,
        )
        net_content = getattr(net_res, "structured_content", None)
        if net_content is None and hasattr(net_res, "content"):
            try:
                net_content = json.loads(str(net_res.content))
            except (json.JSONDecodeError, TypeError):
                net_content = {"ok": False, "error": str(getattr(net_res, "content", "unknown"))}
        steps["network"] = net_content or {"ok": False, "error": "empty network result"}
    else:
        steps["network"] = {"ok": True, "skipped": True, "reason": "sync_network=false"}

    # --- 5. disks ---
    if sync_disks:
        step_num += 1
        db_url_disks = _get_db_url(instance)
        await ctx.info(f"Step {step_num}/{total_steps}: block devices from source")
        steps["disks"] = _migrate_sync_disks(
            source_client,
            src_sid,
            target_client,
            sid,
            dry_run=dry_run,
            db_url=db_url_disks,
        )
    else:
        steps["disks"] = {"ok": True, "skipped": True, "reason": "sync_disks=false"}

    # --- 6. tags ---
    if sync_tags:
        step_num += 1
        await ctx.info(f"Step {step_num}/{total_steps}: tags from source")
        steps["tags"] = _migrate_sync_tags(
            source_client, src_sid, target_client, sid, dry_run=dry_run
        )
    else:
        steps["tags"] = {"ok": True, "skipped": True, "reason": "sync_tags=false"}

    # --- 7. hardware_info (DB) ---
    if sync_hardware_info:
        step_num += 1
        db_url = _get_db_url(instance)
        await ctx.info(f"Step {step_num}/{total_steps}: hardware_info via DB")
        steps["hardware_info"] = _migrate_sync_hardware_info(
            source_client,
            src_sid,
            target_client,
            sid,
            dry_run=dry_run,
            db_url=db_url,
        )
    else:
        steps["hardware_info"] = {"ok": True, "skipped": True, "reason": "sync_hardware_info=false"}

    # --- 8. NUMA + devices (DB) ---
    if sync_numa_devices:
        step_num += 1
        db_url_numa = _get_db_url(instance)
        await ctx.info(f"Step {step_num}/{total_steps}: NUMA topology + devices via DB")
        steps["numa_devices"] = _migrate_sync_numa_and_devices(
            source_client,
            src_sid,
            target_client,
            sid,
            dry_run=dry_run,
            db_url=db_url_numa,
        )
    else:
        steps["numa_devices"] = {"ok": True, "skipped": True, "reason": "sync_numa_devices=false"}

    # --- 9. set_deployed (commissioning scriptset + status=6) ---
    if set_deployed:
        step_num += 1
        db_url_deploy = _get_db_url(instance)
        await ctx.info(f"Step {step_num}/{total_steps}: commissioning scriptset + deployed state")
        steps["commissioning_scriptset"] = _migrate_set_commissioning_scriptset(
            sid,
            dry_run=dry_run,
            db_url=db_url_deploy,
        )
        steps["set_deployed"] = _migrate_set_deployed(
            sid,
            dry_run=dry_run,
            db_url=db_url_deploy,
        )
    else:
        steps["set_deployed"] = {"ok": True, "skipped": True, "reason": "set_deployed=false"}

    all_ok = all(s.get("ok", False) or s.get("skipped", False) for s in steps.values())

    return _tool_result(
        {
            "ok": all_ok,
            "hostname": hostname,
            "system_id": sid,
            "source_system_id": src_sid,
            "source_instance": source_instance,
            "target_instance": instance,
            "dry_run": dry_run,
            "steps": steps,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "bond": {"type": "object"},
            "actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ok", "system_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_create_bond(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    name: Annotated[str, Field(description="Bond interface name")] = "bond0",
    parents: Annotated[
        list[str] | None,
        Field(
            description="Parent interface names (e.g. ['enp48s0np0', 'enp49s0np1']). Auto-detected if omitted."
        ),
    ] = None,
    bond_mode: Annotated[str, Field(description="Bond mode")] = "802.3ad",
    xmit_hash_policy: Annotated[str, Field(description="Transmit hash policy")] = "layer3+4",
    bond_lacp_rate: Annotated[str, Field(description="LACP rate")] = "fast",
    bond_primary: Annotated[
        str | None,
        Field(description="Primary interface name for active-backup bonds (e.g. enp211s0np0)"),
    ] = None,
    mtu: Annotated[int, Field(description="MTU for the bond interface")] = 9000,
    allow_write: bool = False,
) -> ToolResult:
    """Create a bond interface on a MAAS machine (standalone, no source machine needed).

    Resolves the machine, finds parent interfaces by name (or auto-detects the
    two fastest physical interfaces), and POSTs to the MAAS API.

    The machine must be Ready, Allocated, or Deployed.
    Write operation -- requires allow_write=true.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true.")

    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    machine = client.get(f"machines/{sid}")
    hostname = machine.get("hostname", "")
    machine_status = machine.get("status_name", "")

    if machine_status not in _ALLOWED_NETWORK_RECONFIG_STATUSES:
        raise ToolError(
            f"Machine {sid} is in '{machine_status}' state. "
            f"Must be one of {', '.join(_ALLOWED_NETWORK_RECONFIG_STATUSES)} to create bonds."
        )

    iface_set = machine.get("interface_set") or []
    physical = [i for i in iface_set if i.get("type") == "physical"]

    existing_bonds = [i["name"] for i in iface_set if i.get("type") == "bond"]
    if name in existing_bonds:
        raise ToolError(f"Bond '{name}' already exists on {sid} ({hostname}).")

    if parents:
        parent_names = parents
    else:
        ranked = sorted(physical, key=lambda i: i.get("link_speed", 0) or 0, reverse=True)
        if len(ranked) < 2:
            raise ToolError(
                f"Machine {sid} has {len(ranked)} physical interface(s); need at least 2 for a bond."
            )
        parent_names = [ranked[0]["name"], ranked[1]["name"]]
        await ctx.info(f"Auto-detected parents: {parent_names}")

    iface_by_name = {i["name"]: i for i in iface_set}
    parent_ids: list[str] = []
    missing: list[str] = []
    for pname in parent_names:
        iface = iface_by_name.get(pname)
        if iface:
            parent_ids.append(str(iface["id"]))
        else:
            missing.append(pname)

    if missing:
        raise ToolError(f"Parent interfaces not found on {sid}: {missing}")

    create_data: dict[str, Any] = {
        "name": name,
        "parents": parent_ids,
        "bond_mode": bond_mode.replace("+", "%2B"),
        "bond_xmit_hash_policy": xmit_hash_policy.replace("+", "%2B"),
        "bond_lacp_rate": bond_lacp_rate.replace("+", "%2B"),
        "mtu": str(mtu),
    }
    if bond_primary:
        create_data["bond_primary"] = bond_primary

    cancelled = await _confirm_or_proceed(
        ctx,
        f"Confirm: create bond {name} on {hostname} ({sid}) with parents={parent_names}?",
    )
    if cancelled:
        return cancelled

    await ctx.info(f"Creating bond {name} on {sid} with parents={parent_names}")
    result = client.post(
        f"nodes/{sid}/interfaces",
        data=create_data,
        params={"op": "create_bond"},
    )
    bond_id = result.get("id") if isinstance(result, dict) else None

    return _tool_result(
        {
            "ok": True,
            "system_id": sid,
            "hostname": hostname,
            "bond": {"name": name, "id": bond_id, "parents": parent_names},
            "actions": [f"CREATED bond {name} (id={bond_id}) with parents={parent_names}"],
        }
    )


def _find_bond(
    instance: str,
    system_id: str | None,
    machine_id: int | None,
    bond_name: str,
) -> tuple[MaasRestClient, str, str, str, int, dict[str, Any]]:
    """Resolve machine and locate a bond interface.

    Returns (client, sid, hostname, status, bond_interface_id, bond_params).
    """
    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    machine = client.get(f"machines/{sid}")
    hostname = machine.get("hostname", "")
    status = machine.get("status_name", "")

    iface_set = machine.get("interface_set") or []
    bond = next(
        (i for i in iface_set if i.get("type") == "bond" and i.get("name") == bond_name),
        None,
    )
    if not bond:
        raise ToolError(f"No bond interface named '{bond_name}' on {hostname} ({sid}).")

    return client, sid, hostname, status, int(bond["id"]), bond.get("params") or {}


def _maas_bond_put_form_data(
    existing_params: dict[str, Any], target_bond_mode: str
) -> dict[str, str]:
    """Form-encode MAAS interface PUT fields for a bond, preserving timing/MTU."""
    p = existing_params or {}
    mtu = p.get("mtu", 4092)
    miimon = p.get("bond_miimon", 100)
    updelay = p.get("bond_updelay", 0)
    downdelay = p.get("bond_downdelay", 0)
    num_grat = p.get("bond_num_grat_arp", 1)
    lacp = p.get("bond_lacp_rate", "slow")
    xmit = p.get("bond_xmit_hash_policy", "layer2")

    if target_bond_mode == "active-backup":
        xmit = "layer2"
        lacp = "slow"
    elif target_bond_mode == "802.3ad":
        xmit = p.get("bond_xmit_hash_policy") or "layer3+4"
        lacp = p.get("bond_lacp_rate") or "fast"

    def enc(s: str) -> str:
        return str(s).replace("+", "%2B")

    out = {
        "bond_mode": enc(target_bond_mode),
        "bond_miimon": str(miimon),
        "bond_updelay": str(updelay),
        "bond_downdelay": str(downdelay),
        "bond_num_grat_arp": str(num_grat),
        "bond_xmit_hash_policy": enc(str(xmit)),
        "bond_lacp_rate": enc(str(lacp)),
        "mtu": str(mtu),
    }
    if "bond_primary" in p:
        out["bond_primary"] = str(p["bond_primary"])
    return out


def _merge_bond_params_for_db(
    existing: dict[str, Any] | None, target_bond_mode: str
) -> dict[str, Any]:
    out = dict(existing or {})
    out["bond_mode"] = target_bond_mode
    if target_bond_mode == "active-backup":
        out["bond_xmit_hash_policy"] = "layer2"
        out["bond_lacp_rate"] = "slow"
        out.setdefault("bond_miimon", 100)
        out.setdefault("bond_updelay", 0)
        out.setdefault("bond_downdelay", 0)
        out.setdefault("bond_num_grat_arp", 1)
    # Preserve bond_primary if it existed in the original params
    if existing and "bond_primary" in existing:
        out.setdefault("bond_primary", existing["bond_primary"])
    return out


async def _update_bond_params_db(
    ctx: Context,
    instance: str,
    iid: int,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Patch bond params in Postgres via jsonb || merge. Returns final params dict."""
    db_url = _get_db_url(instance)
    if not db_url:
        raise ToolError(
            "database_fallback=true but no database URL: set MAAS_DB_URL or MAAS_<INSTANCE>_DB_URL."
        )

    import json as _json

    import psycopg

    await ctx.warning(f"Patching Postgres maasserver_interface id={iid} params={updates}")

    with psycopg.connect(db_url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE maasserver_interface
                SET params = params || %s::jsonb
                WHERE id = %s AND type = 'bond'
                RETURNING params
                """,
                (_json.dumps(updates), iid),
            )
            row = cur.fetchone()
            if not row:
                raise ToolError(f"DB update affected no row for interface id={iid}")
            db_params = row[0]
        conn.commit()
    return db_params if isinstance(db_params, dict) else {}


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "hostname": {"type": "string"},
            "bond_interface_id": {"type": "integer"},
            "previous_bond_mode": {"type": "string"},
            "bond_mode_after": {"type": "string"},
            "previous_bond_primary": {"type": "string"},
            "bond_primary_after": {"type": "string"},
            "method": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["ok", "system_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_update_bond_mode(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    bond_name: Annotated[str, Field(description="Bond interface name (usually bond0)")] = "bond0",
    bond_mode: Annotated[
        str,
        Field(description="Target Linux bonding mode, e.g. active-backup or 802.3ad"),
    ] = "active-backup",
    bond_primary: Annotated[
        str | None,
        Field(description="Optionally set bond_primary interface at the same time (e.g. enp211s0np0)"),
    ] = None,
    database_fallback: Annotated[
        bool,
        Field(
            description=(
                "If true, after a no-op API response (common on Deployed machines), "
                "patch maasserver_interface.params via MAAS_*_DB_URL when configured."
            ),
        ),
    ] = False,
    allow_write: bool = False,
) -> ToolResult:
    """Change bond_mode (and aligned xmit/LACP fields) on an existing bond.

    Optionally sets ``bond_primary`` in the same call, useful for switching
    to ``active-backup`` mode while designating the primary interface.

    MAAS often returns HTTP 200 on ``PUT .../interfaces/{id}`` for **Deployed**
    machines but **ignores** bond parameter changes. In that case set
    ``database_fallback=true`` and configure ``MAAS_DB_URL`` or
    ``MAAS_<INSTANCE>_DB_URL`` so this tool can update ``maasserver_interface.params``
    directly (same pattern as migrate / NUMA DB sync).

    Write operation — requires ``allow_write=true``.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true.")

    client, sid, hostname, status, iid, prev_params = _find_bond(
        instance, system_id, machine_id, bond_name
    )
    prev_mode = prev_params.get("bond_mode", "")
    prev_primary = prev_params.get("bond_primary", "")

    put_data = _maas_bond_put_form_data(prev_params, bond_mode)
    if bond_primary is not None:
        put_data["bond_primary"] = bond_primary

    primary_desc = f", bond_primary={bond_primary!r}" if bond_primary is not None else ""
    cancelled = await _confirm_or_proceed(
        ctx,
        f"Confirm: set bond '{bond_name}' on {hostname} ({sid}) to bond_mode={bond_mode!r}"
        f"{primary_desc} (currently {prev_mode!r}, status={status})?",
    )
    if cancelled:
        return cancelled

    await ctx.info(f"PUT bond {bond_name} id={iid} bond_mode={bond_mode}{primary_desc}")
    updated = client.put(f"nodes/{sid}/interfaces/{iid}", data=put_data)
    new_params = (updated.get("params") if isinstance(updated, dict) else None) or {}
    new_mode = new_params.get("bond_mode", "")
    new_primary = new_params.get("bond_primary", "")

    mode_ok = new_mode == bond_mode
    primary_ok = bond_primary is None or new_primary == bond_primary

    if mode_ok and primary_ok:
        result: dict[str, Any] = {
            "ok": True,
            "system_id": sid,
            "hostname": hostname,
            "bond_interface_id": iid,
            "previous_bond_mode": prev_mode,
            "bond_mode_after": new_mode,
            "method": "api",
            "note": "Updated via MAAS API.",
        }
        if bond_primary is not None:
            result["previous_bond_primary"] = prev_primary
            result["bond_primary_after"] = new_primary
        return _tool_result(result)

    await ctx.warning(
        f"API PUT did not fully apply changes (bond_mode={new_mode!r}, bond_primary={new_primary!r}); "
        f"MAAS commonly ignores bond changes while Deployed."
    )

    if not database_fallback:
        raise ToolError(
            f"bond_mode is still {new_mode!r} after API PUT. "
            f"Re-run with database_fallback=true and MAAS_*_DB_URL set, "
            f"or release the machine to Ready and use the API again."
        )

    db_updates = _merge_bond_params_for_db(prev_params, bond_mode)
    if bond_primary is not None:
        db_updates["bond_primary"] = bond_primary

    db_params = await _update_bond_params_db(ctx, instance, iid, db_updates)
    final_mode = db_params.get("bond_mode", "")
    final_primary = db_params.get("bond_primary", "")

    result = {
        "ok": final_mode == bond_mode and (bond_primary is None or final_primary == bond_primary),
        "system_id": sid,
        "hostname": hostname,
        "bond_interface_id": iid,
        "previous_bond_mode": prev_mode,
        "bond_mode_after": final_mode,
        "method": "database",
        "note": "Updated maasserver_interface.params via Postgres (API ignored change).",
    }
    if bond_primary is not None:
        result["previous_bond_primary"] = prev_primary
        result["bond_primary_after"] = final_primary
    return _tool_result(result)


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "system_id": {"type": "string"},
            "hostname": {"type": "string"},
            "bond_interface_id": {"type": "integer"},
            "previous_bond_primary": {"type": "string"},
            "bond_primary_after": {"type": "string"},
            "method": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["ok", "system_id"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_update_bond_primary(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    bond_name: Annotated[str, Field(description="Bond interface name (usually bond0)")] = "bond0",
    bond_primary: Annotated[
        str,
        Field(description="Primary interface name for the bond (e.g. enp211s0np0)"),
    ] = "",
    database_fallback: Annotated[
        bool,
        Field(
            description=(
                "If true, after a no-op API response (common on Deployed machines), "
                "patch maasserver_interface.params via MAAS_*_DB_URL when configured."
            ),
        ),
    ] = False,
    allow_write: bool = False,
) -> ToolResult:
    """Change bond_primary on an existing bond interface.

    Sets the primary interface for active-backup bonds.  MAAS often returns
    HTTP 200 on ``PUT .../interfaces/{id}`` for **Deployed** machines but
    **ignores** bond parameter changes.  In that case set
    ``database_fallback=true`` and configure ``MAAS_DB_URL`` or
    ``MAAS_<INSTANCE>_DB_URL`` so this tool can update
    ``maasserver_interface.params`` directly.

    Write operation — requires ``allow_write=true``.
    """
    if not allow_write:
        raise ToolError("Refusing to write: set allow_write=true.")
    if not bond_primary:
        raise ToolError("bond_primary is required (the interface name, e.g. enp211s0np0).")

    client, sid, hostname, status, iid, prev_params = _find_bond(
        instance, system_id, machine_id, bond_name
    )
    prev_primary = prev_params.get("bond_primary", "")
    current_mode = prev_params.get("bond_mode", "")

    put_data = _maas_bond_put_form_data(prev_params, current_mode)
    put_data["bond_primary"] = bond_primary

    cancelled = await _confirm_or_proceed(
        ctx,
        f"Confirm: set bond_primary={bond_primary!r} on '{bond_name}' on {hostname} ({sid}) "
        f"(currently {prev_primary!r}, status={status})?",
    )
    if cancelled:
        return cancelled

    await ctx.info(f"PUT bond {bond_name} id={iid} bond_primary={bond_primary}")
    updated = client.put(f"nodes/{sid}/interfaces/{iid}", data=put_data)
    new_params = (updated.get("params") if isinstance(updated, dict) else None) or {}
    new_primary = new_params.get("bond_primary", "")

    if new_primary == bond_primary:
        return _tool_result(
            {
                "ok": True,
                "system_id": sid,
                "hostname": hostname,
                "bond_interface_id": iid,
                "previous_bond_primary": prev_primary,
                "bond_primary_after": new_primary,
                "method": "api",
                "note": "Updated via MAAS API.",
            }
        )

    await ctx.warning(
        f"API PUT did not apply bond_primary (still {new_primary!r}); "
        f"MAAS commonly ignores bond changes while Deployed."
    )
    if not database_fallback:
        raise ToolError(
            f"bond_primary is still {new_primary!r} after API PUT. "
            f"Re-run with database_fallback=true and MAAS_*_DB_URL set, "
            f"or release the machine to Ready and use the API again."
        )

    db_params = await _update_bond_params_db(ctx, instance, iid, {"bond_primary": bond_primary})
    final_primary = db_params.get("bond_primary", "")

    return _tool_result(
        {
            "ok": final_primary == bond_primary,
            "system_id": sid,
            "hostname": hostname,
            "bond_interface_id": iid,
            "previous_bond_primary": prev_primary,
            "bond_primary_after": final_primary,
            "method": "database",
            "note": "Updated maasserver_interface.params via Postgres (API ignored change).",
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True},
    output_schema={
        "type": "object",
        "properties": {
            "machines": {"type": "array", "items": {"type": "object"}},
            "summary": {"type": "object"},
        },
        "required": ["machines", "summary"],
    },
)
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_preview_netbox_sync(
    ctx: Context,
    instance: InstanceParam = "default",
    source_instance: Annotated[
        str | None,
        Field(description="Source MAAS instance to compare bond config (e.g. 'ori'). Optional."),
    ] = None,
    hostname_filter: Annotated[
        str | None,
        Field(description="Filter machines by hostname prefix (e.g. 'gpu')"),
    ] = None,
) -> ToolResult:
    """Diff report: compare target MAAS, source MAAS, and NetBox for all machines.

    For each machine, reports whether the IP matches NetBox, whether bonds
    match the source MAAS, and what actions ``maas_sync_network_config``
    would perform.  Active cartesia5 devices are flagged as skip.
    """
    target_client = get_client(instance)
    source_client = get_client(source_instance) if source_instance else None
    nb = _get_netbox()

    await ctx.info("Loading machines from target MAAS")
    machines = _normalize_list_response(target_client.get("machines"))

    if hostname_filter:
        machines = [m for m in machines if m.get("hostname", "").startswith(hostname_filter)]

    report: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "ip_ok": 0,
        "ip_mismatch": 0,
        "no_netbox": 0,
        "skip_cartesia5": 0,
        "bond_missing": 0,
    }

    for m in machines:
        counts["total"] += 1
        hostname = m.get("hostname", "")
        maas_ips = m.get("ip_addresses") or []
        status = m.get("status_name", "")

        entry: dict[str, Any] = {
            "hostname": hostname,
            "system_id": m.get("system_id", ""),
            "maas_status": status,
            "maas_ips": maas_ips,
        }

        # NetBox lookup
        nb_device = nb.lookup_device(hostname)
        if nb_device:
            nb_ip = extract_ip(nb_device)
            nb_name = nb_device.get("name", "")
            nb_status = (nb_device.get("status") or {}).get("value", "")
            nb_cluster = (nb_device.get("cluster") or {}).get("name", "")
            entry["netbox_name"] = nb_name
            entry["netbox_ip"] = nb_ip
            entry["netbox_status"] = nb_status
            entry["netbox_cluster"] = nb_cluster

            if nb_status == "active" and nb_cluster == "cartesia5":
                entry["skip"] = True
                entry["skip_reason"] = "active_cartesia5"
                counts["skip_cartesia5"] += 1
            elif nb_ip and nb_ip in maas_ips:
                entry["ip_match"] = True
                counts["ip_ok"] += 1
            elif nb_ip:
                entry["ip_match"] = False
                entry["ip_expected"] = nb_ip
                counts["ip_mismatch"] += 1
            else:
                entry["ip_match"] = None
                entry["note"] = "No primary_ip4 in NetBox"
        else:
            entry["netbox_name"] = None
            counts["no_netbox"] += 1

        # Bond comparison with source
        target_bonds = [
            iface["name"] for iface in (m.get("interface_set") or []) if iface.get("type") == "bond"
        ]
        entry["target_bonds"] = target_bonds

        if source_client and not entry.get("skip"):
            try:
                source_m = _find_source_machine(
                    source_client,
                    source_system_id=None,
                    hostname=hostname,
                    target_iface_set=m.get("interface_set"),
                )
                source_profile = extract_network_profile(source_m)
                source_bond_names = [b["name"] for b in source_profile.get("bonds") or []]
                entry["source_bonds"] = source_bond_names
                missing = set(source_bond_names) - set(target_bonds)
                if missing:
                    entry["bond_missing"] = sorted(missing)
                    counts["bond_missing"] += 1
                else:
                    entry["bond_match"] = True
            except Exception:
                entry["source_match"] = "not_found"

        report.append(entry)
        if counts["total"] % 10 == 0:
            await ctx.report_progress(progress=counts["total"], total=len(machines))

    return _tool_result({"machines": report, "summary": counts})


# ---------------------------------------------------------------------------
# Health endpoint (via mcp-common)
# ---------------------------------------------------------------------------


async def _maas_health_check() -> dict[str, Any]:
    """Readiness check: verify MAAS API connectivity for all instances."""
    checks: dict[str, Any] = {}
    for name, client in maas_clients.items():
        try:
            client.get_version()
            checks[f"maas_{name}"] = {"status": "ok"}
        except Exception:
            checks[f"maas_{name}"] = {"status": "error"}
    return checks


add_health_route(mcp, "maas-mcp", health_check_fn=_maas_health_check)


# ===== Additional Read-Only Tools =====


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_validate_health(
    ctx: Context,
    instance: InstanceParam = "default",
    check_database: Annotated[
        bool, Field(default=True, description="Check DB connectivity and health (requires MAAS_DB_URL)")
    ] = True,
    check_images: Annotated[
        bool, Field(default=True, description="Check boot image sync status")
    ] = True,
) -> dict[str, Any]:
    """Comprehensive MAAS deployment health check.

    Validates 6 categories: controller services (rack + region), database
    connectivity, Squid proxy cache, boot image sync, version consistency,
    and controller IP/DHCP networking.

    Returns structured results with per-controller SSH commands for deeper
    investigation of issues the API cannot cover (proxy cache paths, disk
    usage, template validation, systemd logs).

    Use this tool to triage deployment failures, verify rack health before
    bulk deploys, or diagnose image caching issues.
    """
    client = get_client(instance)
    db_url = _get_db_url(instance) if check_database else None
    from maas_mcp.cli import _validate_health

    result = _validate_health(client, db_url=db_url, check_images=check_images)
    return _safe_dict(result)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_script_results(
    ctx: Context,
    instance: InstanceParam = "default",
    system_id: str | None = None,
    machine_id: int | None = None,
    result_type: Annotated[
        str | None,
        Field(
            default=None,
            description="Filter by type: commissioning, testing, or installation",
        ),
    ] = None,
    fields: FieldsParam = None,
) -> list[dict[str, Any]]:
    """List commissioning, testing, and installation script results for a machine.

    Shows per-script pass/fail status, runtime, and result metadata.
    Useful for diagnosing commissioning or testing failures.
    """
    client = get_client(instance)
    sid = _resolve_system_id(client, system_id, machine_id)
    await ctx.info(f"Fetching script results for {sid}")
    params: dict[str, Any] = {}
    if result_type:
        params["type"] = result_type
    results = _normalize_list_response(client.get(f"nodes/{sid}/results", params=params or None))
    if fields:
        results = [_select_fields(r, fields) for r in results]
    return _safe_list(results)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_tags(
    ctx: Context,
    instance: InstanceParam = "default",
    tag_name: Annotated[
        str | None,
        Field(
            default=None,
            description="If provided, list machines with this tag instead of listing tags",
        ),
    ] = None,
) -> list[dict[str, Any]]:
    """List all tags, or list machines that have a specific tag.

    Without tag_name: returns all tags defined in MAAS.
    With tag_name: returns machines tagged with that name.
    """
    client = get_client(instance)
    if tag_name:
        await ctx.info(f"Listing machines with tag '{tag_name}'")
        return _safe_list(
            _normalize_list_response(client.get(f"tags/{tag_name}", params={"op": "machines"}))
        )
    await ctx.info("Listing all tags")
    return _safe_list(_normalize_list_response(client.get("tags")))


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_subnet_statistics(
    ctx: Context,
    subnet_id: Annotated[int, Field(description="Subnet ID to get statistics for")],
    instance: InstanceParam = "default",
    include_ranges: Annotated[
        bool,
        Field(default=False, description="Also return reserved and unreserved IP ranges"),
    ] = False,
    include_addresses: Annotated[
        bool,
        Field(default=False, description="Also return individual IP address summary"),
    ] = False,
) -> dict[str, Any]:
    """Get IP utilization statistics for a subnet.

    Returns total IPs, available, used, and utilization percentage.
    Optionally includes reserved/unreserved ranges and per-IP summaries.
    """
    client = get_client(instance)
    await ctx.info(f"Fetching statistics for subnet {subnet_id}")
    result: dict[str, Any] = {
        "subnet_id": subnet_id,
        "statistics": client.get(f"subnets/{subnet_id}", params={"op": "statistics"}),
    }
    if include_ranges:
        result["reserved_ranges"] = client.get(
            f"subnets/{subnet_id}", params={"op": "reserved_ip_ranges"}
        )
        result["unreserved_ranges"] = client.get(
            f"subnets/{subnet_id}", params={"op": "unreserved_ip_ranges"}
        )
    if include_addresses:
        result["ip_addresses"] = client.get(f"subnets/{subnet_id}", params={"op": "ip_addresses"})
    return _safe_dict(result)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_get_config(
    ctx: Context,
    name: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Config key to read (e.g. default_osystem, default_distro_series, "
                "commissioning_distro_series, kernel_opts, maas_name, ntp_servers, "
                "upstream_dns, http_proxy). If omitted, returns all known config keys."
            ),
        ),
    ] = None,
    instance: InstanceParam = "default",
) -> dict[str, Any]:
    """Read MAAS global configuration values.

    With a specific key, returns that single value. Without a key, reads
    a set of commonly useful config keys.
    """
    client = get_client(instance)
    if name:
        await ctx.info(f"Reading config '{name}'")
        value = client.get("maas", params={"op": "get_config", "name": name})
        return _safe_dict({"name": name, "value": value})

    await ctx.info("Reading all common config keys")
    keys = [
        "maas_name",
        "default_osystem",
        "default_distro_series",
        "commissioning_distro_series",
        "kernel_opts",
        "ntp_servers",
        "ntp_external_only",
        "upstream_dns",
        "http_proxy",
        "enable_analytics",
        "completed_intro",
        "network_discovery",
        "active_discovery_interval",
        "default_min_hwe_kernel",
        "remote_syslog",
        "maas_auto_ipmi_user",
    ]
    config: dict[str, Any] = {}
    for k in keys:
        try:
            config[k] = client.get("maas", params={"op": "get_config", "name": k})
        except Exception:
            config[k] = None
    return _safe_dict(config)


_RESOURCE_TYPES = {
    "discoveries": "discovery",
    "resource_pools": "resourcepools",
    "notifications": "notifications",
    "dhcp_snippets": "dhcp-snippets",
    "region_controllers": "regioncontrollers",
    "scripts": "scripts",
}


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_resources(
    ctx: Context,
    resource_type: Annotated[
        str,
        Field(
            description=(
                "Type of resource: discoveries, resource_pools, notifications, "
                "dhcp_snippets, region_controllers, scripts"
            ),
        ),
    ],
    instance: InstanceParam = "default",
    script_type: Annotated[
        str | None,
        Field(default=None, description="Filter scripts by type: commissioning or testing"),
    ] = None,
    fields: FieldsParam = None,
) -> list[dict[str, Any]]:
    """List MAAS resources by type.

    Supported types:
    - discoveries: unknown devices found on network (pre-enlistment)
    - resource_pools: machine partitioning groups (RBAC/workload isolation)
    - notifications: system alerts and messages
    - dhcp_snippets: custom DHCP configuration
    - region_controllers: region controller health
    - scripts: stored commissioning/testing scripts (use script_type to filter)
    """
    rt = resource_type.strip().lower()
    endpoint = _RESOURCE_TYPES.get(rt)
    if not endpoint:
        raise ValueError(
            f"resource_type must be one of {list(_RESOURCE_TYPES)}, got '{resource_type}'"
        )

    client = get_client(instance)
    await ctx.info(f"Listing {rt} from '{instance}'")
    params: dict[str, Any] = {}
    if rt == "scripts" and script_type:
        params["type"] = script_type
    results = _normalize_list_response(client.get(endpoint, params=params or None))
    if fields:
        results = [_select_fields(r, fields) for r in results]
    return _safe_list(results)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_users(
    ctx: Context,
    instance: InstanceParam = "default",
    whoami: Annotated[
        bool,
        Field(default=False, description="Return only the currently authenticated user"),
    ] = False,
) -> dict[str, Any] | list[dict[str, Any]]:
    """List MAAS users or get the currently authenticated user (whoami)."""
    client = get_client(instance)
    if whoami:
        await ctx.info("Getting current user (whoami)")
        return _safe_dict(client.get("users", params={"op": "whoami"}))
    await ctx.info("Listing all users")
    return _safe_list(_normalize_list_response(client.get("users")))


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_list_ip_addresses(
    ctx: Context,
    instance: InstanceParam = "default",
    include_reserved: Annotated[
        bool,
        Field(default=False, description="Also include reserved IP addresses"),
    ] = False,
) -> dict[str, Any]:
    """List individual IP address allocations from MAAS.

    Returns per-IP assignments (which machine, which interface, what mode).
    Optionally includes reserved IPs (static reservations).

    See also: maas_ip_ranges for DHCP pools and reserved blocks,
    maas_subnet_statistics for utilization percentages per subnet.
    """
    client = get_client(instance)
    await ctx.info("Listing IP addresses")
    result: dict[str, Any] = {
        "ip_addresses": _safe_list(_normalize_list_response(client.get("ipaddresses"))),
    }
    if include_reserved:
        result["reserved_ips"] = _safe_list(_normalize_list_response(client.get("reservedips")))
    return _safe_dict(result)


# ---------------------------------------------------------------------------
# Bond audit
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/maas-mcp")
async def maas_bond_audit(
    ctx: Context,
    instance: InstanceParam = "default",
    hostname: Annotated[
        str | None,
        Field(default=None, description="Single MAAS hostname to audit"),
    ] = None,
    hostnames: Annotated[
        list[str] | None,
        Field(default=None, description="List of MAAS hostnames to audit"),
    ] = None,
    cluster: Annotated[
        str | None,
        Field(default=None, description="NetBox cluster name — resolves all active nodes"),
    ] = None,
    mismatches_only: Annotated[
        bool,
        Field(default=False, description="Only return mismatches and errors"),
    ] = False,
    bond_name: Annotated[
        str,
        Field(default="bond0", description="Bond interface name to audit"),
    ] = "bond0",
) -> ToolResult:
    """Compare live bond active slave (SSH) vs MAAS bond configuration.

    Detects when the active NIC on a node's bond0 differs from what MAAS
    records as the primary parent. Useful for verifying bond failover state
    and catching persistent misconfigurations.

    Provide at least one of: hostname, hostnames, or cluster.
    """
    from maas_mcp.bond_audit import (
        async_ssh_bond_info,
        build_audit_result,
        build_summary,
        extract_maas_bond_config,
        resolve_cluster_hostnames,
        resolve_maas_hostnames,
    )

    if not hostname and not hostnames and not cluster:
        raise ToolError("Provide at least one of: hostname, hostnames, or cluster")

    client = get_client(instance)
    hosts: list[str] = []

    if cluster:
        nb = _get_netbox()
        await ctx.info(f"Resolving cluster {cluster!r} via NetBox")
        resolved = resolve_cluster_hostnames(nb, cluster)
        if not resolved:
            raise ToolError(f"No active devices found in NetBox cluster {cluster!r}")
        for entry in resolved:
            hosts.append(entry["maas_hostname"])
        await ctx.info(f"Resolved {len(resolved)} nodes from cluster {cluster!r}")

    if hostname:
        if hostname not in hosts:
            hosts.append(hostname)
    if hostnames:
        for h in hostnames:
            if h not in hosts:
                hosts.append(h)

    await ctx.info(f"Auditing bond {bond_name!r} on {len(hosts)} node(s)")

    maas_data = resolve_maas_hostnames(client, hosts)

    ssh_tasks = {h: async_ssh_bond_info(h, bond_name) for h in hosts}
    ssh_results_list = await asyncio.gather(*ssh_tasks.values(), return_exceptions=True)
    ssh_results: dict[str, dict[str, Any]] = {}
    for h, res in zip(ssh_tasks.keys(), ssh_results_list, strict=True):
        if isinstance(res, Exception):
            ssh_results[h] = {"active_slave": None, "slaves": [], "error": str(res)}
        else:
            ssh_results[h] = res

    results: list[dict[str, Any]] = []
    for h in hosts:
        maas_info = maas_data.get(h, {})
        system_id = maas_info.get("system_id")
        machine = maas_info.get("machine")
        maas_error = maas_info.get("error")

        if maas_error:
            entry = build_audit_result(h, system_id, ssh_results.get(h, {}), None)
            entry["error"] = maas_error
            results.append(entry)
            continue

        maas_bond = extract_maas_bond_config(machine, bond_name) if machine else None
        results.append(build_audit_result(h, system_id, ssh_results.get(h, {}), maas_bond))

    if mismatches_only:
        results = [r for r in results if r.get("match") is False or r.get("error")]

    summary = build_summary(results)
    return _tool_result({"results": results, "summary": summary})


# ---------------------------------------------------------------------------
# Server initialization (shared by CLI and ASGI)
# ---------------------------------------------------------------------------

_initialized = False


def _initialize(settings: Settings) -> None:
    """Initialize MAAS clients and middleware from settings. Idempotent."""
    global _initialized
    if _initialized:
        return

    configure_logging(settings.log_level)

    instances = settings.get_maas_instances()
    for name, instance_config in instances.items():
        try:
            client = MaasRestClient(
                url=str(instance_config.url),
                api_key=instance_config.api_key.get_secret_value(),
                verify_ssl=settings.verify_ssl,
                timeout_seconds=settings.timeout_seconds,
            )
            maas_clients[name] = client
            logger.info("Initialized MAAS instance '%s' at %s", name, instance_config.url)
        except Exception as e:
            logger.error("Failed to initialize MAAS instance '%s': %s", name, e)
            raise

    if settings.maas_db_url:
        maas_db_urls["default"] = settings.maas_db_url
    for name in list(maas_clients):
        if name == "default":
            continue
        env_key = f"MAAS_{name.upper()}_DB_URL"
        db_val = os.environ.get(env_key)
        if db_val:
            maas_db_urls[name] = db_val
            logger.info("Registered DB URL for instance '%s'", name)
    default_site = (settings.maas_default_site or "").lower()
    if default_site and default_site in maas_db_urls and "default" not in maas_db_urls:
        maas_db_urls["default"] = maas_db_urls[default_site]

    global netbox
    if settings.netbox_url and settings.netbox_token:
        netbox = NetboxClient(
            url=str(settings.netbox_url),
            token=settings.netbox_token.get_secret_value(),
        )
        logger.info("Initialized NetBox client at %s", settings.netbox_url)
    else:
        logger.info("NetBox not configured -- network-sync tools will be unavailable")

    def cleanup_clients() -> None:
        for c in maas_clients.values():
            try:
                c.close()
            except Exception:
                pass

    atexit.register(cleanup_clients)

    logger.info("MAAS MCP Server starting with config: %s", settings.get_effective_config_summary())
    _initialized = True


# ---------------------------------------------------------------------------
# ASGI app factory (for uvicorn / K8s deployment)
# ---------------------------------------------------------------------------


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage:
        uvicorn maas_mcp.server:create_app --factory --host 0.0.0.0 --port 8000

    Configuration is read from environment variables / .env files
    (no CLI args in ASGI mode).
    """
    settings = Settings()
    _initialize(settings)

    token = (
        settings.mcp_http_access_token.get_secret_value()
        if settings.mcp_http_access_token
        else None
    )
    return create_http_app(
        mcp, path="/mcp", auth_token=token, stateless_http=settings.stateless_http
    )


# ---------------------------------------------------------------------------
# CLI entry point (stdio or direct HTTP)
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: ``maas-mcp`` command."""
    suppress_ssl_warnings()
    overlay = parse_cli_args()

    try:
        settings = Settings(**overlay)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _initialize(settings)
    except Exception as e:
        logger.error("Failed to initialize: %s", e)
        sys.exit(1)

    try:
        if settings.transport == "stdio":
            logger.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            if settings.mcp_http_access_token:
                mcp.add_middleware(
                    HttpAccessTokenAuth(settings.mcp_http_access_token.get_secret_value())
                )
            logger.info("Starting HTTP transport on %s:%s", settings.host, settings.port)
            mcp.run(host=settings.host, port=settings.port, transport="http")
    except Exception as e:
        logger.error("Failed to start MCP server: %s", e)
        sys.exit(1)
