"""
redfish-cli: Thin CLI wrapper around the Redfish BMC API.

Provides the same capabilities as redfish-mcp but via shell commands,
enabling AI agents to use Redfish with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import json
from typing import Any

import requests
import typer

_ReadTimeout = requests.exceptions.ReadTimeout

try:
    from mcp_common.agent_remediation import install_cli_exception_handler
except ModuleNotFoundError:

    def install_cli_exception_handler(_app, project_repo: str) -> None:  # type: ignore[no-redef]
        # Compatibility fallback when mcp-common is unavailable in minimal test envs.
        return None


from redfish_mcp.kvm.cli_commands import app as _kvm_app
from redfish_mcp.power_actions import InvalidActionError, resolve_reset_type
from redfish_mcp.redfish import RedfishClient

app = typer.Typer(
    name="redfish-cli",
    help="Query and manage BMC/Redfish hardware. Use --help on any subcommand.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/redfish-mcp")
app.add_typer(_kvm_app, name="kvm")

_cli_user: str | None = None
_cli_password: str | None = None


@app.callback()
def _main_callback(
    user: str | None = typer.Option(
        None, "--user", "-U", envvar="REDFISH_USER", help="BMC username (default: $REDFISH_USER)"
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-P",
        envvar="REDFISH_PASSWORD",
        help="BMC password (default: $REDFISH_PASSWORD)",
    ),
) -> None:
    global _cli_user, _cli_password
    _cli_user = user
    _cli_password = password


def _creds(host: str = "") -> tuple[str, str]:
    user = _cli_user or ""
    password = _cli_password or ""
    if not user or not password:
        from redfish_mcp.agent_controller import _resolve_env_credentials

        env_creds = _resolve_env_credentials(host)
        if env_creds:
            if not user:
                user = env_creds[0]
            if not password:
                password = env_creds[1]
    if not user or not password:
        typer.echo(
            "Error: REDFISH_USER and REDFISH_PASSWORD env vars required (or use --user/--password)",
            err=True,
        )
        raise typer.Exit(1)
    return user, password


def _client(host: str, verify_tls: bool = False, timeout_s: int = 30) -> RedfishClient:
    user, password = _creds(host)
    return RedfishClient(
        host=host,
        user=user,
        password=password,
        verify_tls=verify_tls,
        timeout_s=timeout_s,
    )


def _output(data: Any, as_json: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, dict):
        if not data.get("ok", True):
            typer.echo(f"ERROR: {data.get('error', 'unknown')}", err=True)
            return
        for k, v in data.items():
            if k in ("ok",):
                continue
            if isinstance(v, dict):
                typer.echo(f"  {k}:")
                for k2, v2 in v.items():
                    typer.echo(f"    {k2}: {v2}")
            elif isinstance(v, list):
                typer.echo(f"  {k}: [{len(v)} items]")
                for item in v[:20]:
                    if isinstance(item, dict):
                        line = "  ".join(f"{ik}={iv}" for ik, iv in item.items())
                        typer.echo(f"    {line}")
                    else:
                        typer.echo(f"    {item}")
                if len(v) > 20:
                    typer.echo(f"    ... and {len(v) - 20} more")
            else:
                typer.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            typer.echo(item)
    else:
        typer.echo(data)


def _system_summary(system: dict) -> dict[str, Any]:
    return {
        "Manufacturer": system.get("Manufacturer"),
        "Model": system.get("Model"),
        "SerialNumber": system.get("SerialNumber"),
        "BiosVersion": system.get("BiosVersion"),
        "PowerState": system.get("PowerState"),
        "Status": system.get("Status"),
    }


@app.command()
def info(
    host: str = typer.Argument(help="BMC IP or hostname (use oob_ip from NetBox)"),
    info_types: str | None = typer.Option(
        "system,boot",
        "--types",
        "-t",
        help="Comma-separated: system,boot,bios_current,bios_pending,drives,power,thermal,processors,memory,pcie_devices,manager,manager_ethernet,all",
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get system information (model, health, boot config, drives)."""
    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()

    valid_types = {
        "system",
        "boot",
        "bios_current",
        "bios_pending",
        "drives",
        "power",
        "thermal",
        "processors",
        "memory",
        "pcie_devices",
        "manager",
        "manager_ethernet",
        "all",
    }
    types = [t.strip() for t in (info_types or "system,boot").split(",")]

    unknown = [t for t in types if t not in valid_types]
    if unknown:
        typer.echo(f"Error: unknown info_type(s): {', '.join(unknown)}", err=True)
        typer.echo(f"Valid types: {', '.join(sorted(valid_types))}", err=True)
        raise typer.Exit(1)

    if "all" in types:
        types = [
            "system",
            "boot",
            "bios_current",
            "bios_pending",
            "drives",
            "power",
            "thermal",
            "processors",
            "memory",
            "pcie_devices",
            "manager",
            "manager_ethernet",
        ]

    result: dict[str, Any] = {"ok": True, "host": host}

    system = c.get_json(ep.system_url)

    if "system" in types:
        result["system"] = _system_summary(system)

    if "boot" in types:
        boot = system.get("Boot") or {}
        result["boot"] = {
            "BootSourceOverrideEnabled": boot.get("BootSourceOverrideEnabled"),
            "BootSourceOverrideTarget": boot.get("BootSourceOverrideTarget"),
            "BootSourceOverrideMode": boot.get("BootSourceOverrideMode"),
        }

    if "drives" in types:
        from redfish_mcp.inventory import collect_drive_inventory

        inv = collect_drive_inventory(c, ep, nvme_only=True)
        result["drives"] = {"count": inv.get("count", 0), "drives": inv.get("drives", [])}

    if "bios_current" in types:
        from redfish_mcp.bios_diff import get_bios_attributes

        attrs, url, err = get_bios_attributes(c, ep)
        if err:
            result["bios_current"] = {"error": err}
        else:
            result["bios_current"] = {"url": url, "count": len(attrs) if attrs else 0}

    if "power" in types:
        from redfish_mcp.chassis_telemetry import collect_power_info

        power_data = collect_power_info(c)
        power_data.pop("sources", None)
        power_data.pop("errors", None)
        result["power"] = power_data

    if "thermal" in types:
        from redfish_mcp.chassis_telemetry import collect_thermal_info

        thermal_data = collect_thermal_info(c)
        thermal_data.pop("sources", None)
        thermal_data.pop("errors", None)
        result["thermal"] = thermal_data

    if "processors" in types:
        from redfish_mcp.system_inventory import collect_processor_inventory

        proc_data = collect_processor_inventory(c, ep)
        proc_data.pop("sources", None)
        proc_data.pop("errors", None)
        result["processors"] = proc_data

    if "memory" in types:
        from redfish_mcp.system_inventory import collect_memory_inventory

        mem_data = collect_memory_inventory(c, ep)
        mem_data.pop("sources", None)
        mem_data.pop("errors", None)
        result["memory"] = mem_data

    if "pcie_devices" in types:
        from redfish_mcp.system_inventory import collect_pcie_inventory

        pcie_data = collect_pcie_inventory(c, ep)
        pcie_data.pop("sources", None)
        pcie_data.pop("errors", None)
        result["pcie_devices"] = pcie_data

    if "manager" in types:
        from redfish_mcp.manager_info import collect_manager_info

        mgr_data = collect_manager_info(c)
        mgr_data.pop("sources", None)
        mgr_data.pop("errors", None)
        result["manager_info"] = mgr_data

    if "manager_ethernet" in types:
        from redfish_mcp.manager_info import collect_manager_ethernet

        eth_data = collect_manager_ethernet(c)
        eth_data.pop("sources", None)
        eth_data.pop("errors", None)
        result["manager_ethernet"] = eth_data

    _output(result, as_json=json_output)


@app.command()
def health(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Quick health check: power state, health status, BIOS version."""
    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    system = c.get_json(ep.system_url)
    status = system.get("Status") or {}

    result = {
        "ok": True,
        "host": host,
        "PowerState": system.get("PowerState"),
        "Health": status.get("Health"),
        "HealthRollup": status.get("HealthRollup"),
        "State": status.get("State"),
        "Manufacturer": system.get("Manufacturer"),
        "Model": system.get("Model"),
        "BiosVersion": system.get("BiosVersion"),
    }
    _output(result, as_json=json_output)


@app.command()
def firmware(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List firmware inventory for all components (BIOS, BMC, NIC, GPU, etc.)."""
    from redfish_mcp.firmware_inventory import collect_firmware_inventory

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    inventory = collect_firmware_inventory(c, ep)

    if json_output:
        _output({"ok": True, "host": host, **inventory}, as_json=True)
        return

    typer.echo(f"# Firmware inventory for {host}")
    categories = inventory.get("categories", {})
    if isinstance(categories, dict):
        for cat, items in categories.items():
            typer.echo(f"\n## {cat}")
            if isinstance(items, list):
                for item in items:
                    name = item.get("Name", item.get("name", "?"))
                    ver = item.get("Version", item.get("version", "?"))
                    typer.echo(f"  {name}: {ver}")
    else:
        components = inventory.get("components", inventory.get("firmware", []))
        if isinstance(components, list):
            for item in components:
                name = item.get("Name", item.get("name", "?"))
                ver = item.get("Version", item.get("version", "?"))
                typer.echo(f"  {name}: {ver}")
        else:
            _output({"ok": True, "host": host, **inventory})


@app.command()
def sensors(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Read thermal sensors and fan speeds from the BMC."""
    c = _client(host, verify_tls, timeout)

    chassis_url = f"{c.base_url}/redfish/v1/Chassis"
    chassis_data, err = c.get_json_maybe(chassis_url)
    if err or not chassis_data:
        typer.echo(f"Error: Cannot reach Chassis endpoint: {err}", err=True)
        raise typer.Exit(1)

    from redfish_mcp.redfish import filter_host_chassis, to_abs

    members = filter_host_chassis(chassis_data.get("Members", []))
    if not members:
        typer.echo("Error: No host chassis members found", err=True)
        raise typer.Exit(1)

    chassis_path = members[0].get("@odata.id", "")
    chassis_member_url = to_abs(c.base_url, chassis_path)

    thermal_url = f"{chassis_member_url}/Thermal"
    thermal, terr = c.get_json_maybe(thermal_url)

    result: dict[str, Any] = {"ok": True, "host": host, "temperatures": [], "fans": []}

    if thermal and not terr:
        for t in thermal.get("Temperatures", []):
            result["temperatures"].append(
                {
                    "Name": t.get("Name"),
                    "ReadingCelsius": t.get("ReadingCelsius"),
                    "Status": t.get("Status", {}).get("Health"),
                    "UpperCritical": t.get("UpperThresholdCritical"),
                }
            )
        for f in thermal.get("Fans", []):
            result["fans"].append(
                {
                    "Name": f.get("Name") or f.get("FanName"),
                    "Reading": f.get("Reading"),
                    "Units": f.get("ReadingUnits"),
                    "Status": f.get("Status", {}).get("Health"),
                }
            )
    else:
        telemetry_url = f"{chassis_member_url}/ThermalSubsystem"
        telemetry, _ = c.get_json_maybe(telemetry_url)
        if telemetry:
            result["note"] = "Redfish ThermalSubsystem detected; use --json for raw data"
            result["thermal_subsystem"] = telemetry_url

    _output(result, as_json=json_output)


@app.command()
def query(
    host: str = typer.Argument(help="BMC IP or hostname"),
    query_type: str = typer.Argument(
        help="Query type: health, power_state, boot_setting, bios_attribute, list_nics, list_bios_attributes"
    ),
    key: str | None = typer.Option(None, "--key", "-k", help="Attribute key to query"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Query specific settings (health, power, boot, BIOS attribute, NICs)."""
    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    system = c.get_json(ep.system_url)

    result: dict[str, Any] = {"ok": True, "host": host, "query_type": query_type}

    if query_type == "health":
        status = system.get("Status") or {}
        result["Health"] = status.get("Health")
        result["HealthRollup"] = status.get("HealthRollup")
        result["State"] = status.get("State")

    elif query_type == "power_state":
        result["PowerState"] = system.get("PowerState")

    elif query_type == "boot_setting":
        boot = system.get("Boot") or {}
        result["BootSourceOverrideEnabled"] = boot.get("BootSourceOverrideEnabled")
        result["BootSourceOverrideTarget"] = boot.get("BootSourceOverrideTarget")
        result["BootSourceOverrideMode"] = boot.get("BootSourceOverrideMode")

    elif query_type == "bios_attribute":
        if not key:
            typer.echo("Error: --key required for bios_attribute query", err=True)
            raise typer.Exit(1)
        from redfish_mcp.bios_diff import get_bios_attributes

        attrs, _, err = get_bios_attributes(c, ep)
        if err or not attrs:
            result["ok"] = False
            result["error"] = err or "Failed to get BIOS attributes"
        elif key in attrs:
            result["attribute"] = key
            result["value"] = attrs[key]
        else:
            result["ok"] = False
            result["error"] = f"Attribute '{key}' not found"
            similar = [k for k in attrs if key.lower() in k.lower()][:10]
            if similar:
                result["similar_keys"] = similar

    elif query_type == "list_bios_attributes":
        from redfish_mcp.bios_diff import get_bios_attributes

        attrs, _, err = get_bios_attributes(c, ep)
        if err or not attrs:
            result["ok"] = False
            result["error"] = err or "Failed to get BIOS attributes"
        else:
            if key:
                filtered = {k: v for k, v in attrs.items() if key.lower() in k.lower()}
                result["attributes"] = filtered
                result["count"] = len(filtered)
            else:
                result["attribute_keys"] = sorted(attrs.keys())
                result["count"] = len(attrs)

    elif query_type == "list_nics":
        from redfish_mcp.redfish import to_abs

        for base in ["/NetworkInterfaces", "/EthernetInterfaces"]:
            nics_url = f"{ep.system_url}{base}"
            nics_coll, nics_err = c.get_json_maybe(nics_url)
            if not nics_err and nics_coll:
                nic_list = []
                for m in nics_coll.get("Members", [])[:20]:
                    if isinstance(m, dict) and "@odata.id" in m:
                        nic_url = to_abs(c.base_url, m["@odata.id"])
                        nic, _ = c.get_json_maybe(nic_url)
                        if nic:
                            nic_list.append(
                                {
                                    "Id": nic.get("Id"),
                                    "Name": nic.get("Name"),
                                    "MACAddress": nic.get("MACAddress"),
                                    "LinkStatus": nic.get("LinkStatus"),
                                    "SpeedMbps": nic.get("SpeedMbps"),
                                }
                            )
                result["nics"] = nic_list
                result["count"] = len(nic_list)
                break
        if "nics" not in result:
            result["ok"] = False
            result["error"] = "Could not find NIC endpoints"

    else:
        result["ok"] = False
        result["error"] = f"Unknown query_type: {query_type}"
        result["supported"] = [
            "health",
            "power_state",
            "boot_setting",
            "bios_attribute",
            "list_bios_attributes",
            "list_nics",
        ]
        result["mcp_only"] = ["bmc_log_services", "nic_pxe"]
        result["hint"] = "Some query_types are only available via the MCP tool redfish_query"

    _output(result, as_json=json_output)


_KNOWN_LOG_SERVICES = ["Sel", "Log1", "Lclog", "FaultList", "EventLog"]

LOG_SERVICE_ALIASES: dict[str, list[str]] = {
    "sel": ["Sel", "Log1", "SEL"],
    "lclog": ["Lclog", "LC.Log", "Log2"],
    "faultlist": ["FaultList", "Log3"],
}


def _first_manager_path(c: RedfishClient) -> str | None:
    """Return the @odata.id of the host-server Manager, or None.

    Prefers ``iDRAC.Embedded.1`` over ``HGX_*`` managers on multi-manager
    systems like the Dell B300.
    """
    from redfish_mcp.redfish import _pick_host_manager

    mgr_root, err = c.get_json_maybe(f"{c.base_url}/redfish/v1/Managers")
    if err or not mgr_root:
        return None
    members = mgr_root.get("Members", [])
    if not members:
        return None
    try:
        chosen = _pick_host_manager(members)
    except RuntimeError:
        return None
    return chosen.get("@odata.id", "") or None


def _resolve_alias(service: str, available_names: set[str]) -> str | None:
    """Try to resolve *service* via LOG_SERVICE_ALIASES against *available_names*."""
    aliases = LOG_SERVICE_ALIASES.get(service.lower(), [])
    for alias in aliases:
        if alias in available_names:
            return alias
    return None


def _discover_log_service(c: RedfishClient, service: str | None) -> tuple[str, str]:
    """Resolve a log service Entries URL.

    When *service* is given, tries exact match on iDRAC then the first
    generic manager, then alias mapping, then case-insensitive match
    against all discovered services.

    When *service* is None, tries iDRAC Sel as a fast path, then
    enumerates all LogServices and picks the best match.

    Returns (entries_url_without_query_params, service_name).
    Raises RuntimeError on failure with an agent-friendly message.
    """
    from redfish_mcp.redfish import to_abs

    def _service_reachable(svc_url: str) -> bool:
        data, err = c.get_json_maybe(svc_url)
        return err is None and data is not None

    idrac_base = f"{c.base_url}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices"

    if service:
        if _service_reachable(f"{idrac_base}/{service}"):
            return f"{idrac_base}/{service}/Entries", service
        mgr_path = _first_manager_path(c)
        if mgr_path:
            svc_url = to_abs(c.base_url, f"{mgr_path}/LogServices/{service}")
            if _service_reachable(svc_url):
                return f"{svc_url}/Entries", service

        available = _enumerate_all_log_services(c)
        available_names = {n for n, _ in available}

        alias_match = _resolve_alias(service, available_names)
        if alias_match:
            for svc_name, odata_id in available:
                if svc_name == alias_match:
                    return to_abs(c.base_url, f"{odata_id}/Entries"), svc_name

        for svc_name, odata_id in available:
            if svc_name.lower() == service.lower():
                return to_abs(c.base_url, f"{odata_id}/Entries"), svc_name

        avail_names = ", ".join(n for n, _ in available) if available else "none found"
        msg = f"Log service '{service}' not found. Available services: {avail_names}"
        raise RuntimeError(msg)

    if _service_reachable(f"{idrac_base}/Sel"):
        return f"{idrac_base}/Sel/Entries", "Sel"

    available = _enumerate_all_log_services(c)
    if not available:
        msg = "No log services found on this BMC"
        raise RuntimeError(msg)

    for priority_name in _KNOWN_LOG_SERVICES:
        for svc_name, odata_id in available:
            if svc_name.lower() == priority_name.lower():
                return to_abs(c.base_url, f"{odata_id}/Entries"), svc_name

    svc_name, odata_id = available[0]
    return to_abs(c.base_url, f"{odata_id}/Entries"), svc_name


def _enumerate_all_log_services(
    c: RedfishClient,
) -> list[tuple[str, str]]:
    """Return [(service_name, odata_id), ...] from Manager and System LogServices."""
    from redfish_mcp.redfish import to_abs

    available: list[tuple[str, str]] = []
    seen: set[str] = set()

    mgr_path = _first_manager_path(c)
    if mgr_path:
        _collect_log_services(c, to_abs(c.base_url, f"{mgr_path}/LogServices"), available, seen)

    ep = None
    try:
        ep = c.discover_system()
    except Exception:
        pass
    if ep:
        _collect_log_services(c, f"{ep.system_url}/LogServices", available, seen)

    return available


def _collect_log_services(
    c: RedfishClient,
    collection_url: str,
    out: list[tuple[str, str]],
    seen: set[str],
) -> None:
    """Append (name, odata_id) tuples from a LogServices collection URL."""
    svc_data, err = c.get_json_maybe(collection_url)
    if err or not svc_data:
        return
    for member in svc_data.get("Members", []):
        odata_id = member.get("@odata.id", "")
        if odata_id and odata_id not in seen:
            svc_name = odata_id.rstrip("/").rsplit("/", 1)[-1]
            seen.add(odata_id)
            out.append((svc_name, odata_id))


@app.command()
def logs(
    host: str = typer.Argument(help="BMC IP or hostname"),
    log_service: str | None = typer.Option(
        None,
        "--service",
        "-s",
        help="Log service name (auto-detected if omitted). Examples: Sel, Log1, Lclog, FaultList",
    ),
    discover: bool = typer.Option(
        False,
        "--discover",
        "-d",
        help="List available log services without fetching entries",
    ),
    limit: int = typer.Option(50, "--limit", "-l"),
    severity: str | None = typer.Option(None, "--severity", help="Filter: Warning, Critical, OK"),
    date: str | None = typer.Option(None, "--date", help="ISO-8601 date prefix filter"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Read BMC log entries (SEL, Lifecycle, FaultList, Supermicro MEL, etc.).

    Auto-detects available log services on Dell iDRAC and Supermicro BMCs.
    Use --service to target a specific service by name, or --discover to
    list available services without fetching entries.
    """
    c = _client(host, verify_tls, timeout)

    if discover:
        available = _enumerate_all_log_services(c)
        services = [{"name": n, "url": u} for n, u in available]
        _output(
            {"ok": True, "host": host, "log_services": services, "count": len(services)},
            as_json=json_output,
        )
        return

    limit = min(max(limit, 1), 500)

    try:
        entries_url, resolved_service = _discover_log_service(c, log_service)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    url_with_query = f"{entries_url}?$top={limit}"
    data, err = c.get_json_maybe(url_with_query)
    if err or not data:
        typer.echo(f"Error: Failed to read {resolved_service} entries: {err}", err=True)
        raise typer.Exit(1)

    raw = data.get("Members", [])

    raw.sort(key=lambda e: e.get("Created", ""), reverse=True)

    entries = []
    for entry in raw:
        created = entry.get("Created", "")
        sev = entry.get("Severity", "")
        if date and not created.startswith(date):
            continue
        if severity and sev.lower() != severity.lower():
            continue
        entries.append(
            {
                "id": entry.get("Id"),
                "created": created,
                "severity": sev,
                "message": entry.get("Message", ""),
            }
        )

    result = {
        "ok": True,
        "host": host,
        "log_service": resolved_service,
        "count": len(entries),
        "entries": entries,
    }
    _output(result, as_json=json_output)


@app.command()
def users(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List BMC user accounts."""
    from redfish_mcp.redfish import to_abs

    c = _client(host, verify_tls, timeout)

    acct_svc_url = f"{c.base_url}/redfish/v1/AccountService"
    acct_svc, err = c.get_json_maybe(acct_svc_url)
    if err or not acct_svc:
        typer.echo(f"Error: Cannot reach AccountService: {err}", err=True)
        raise typer.Exit(1)

    accts_obj = acct_svc.get("Accounts")
    accts_rel = (
        accts_obj.get("@odata.id") if isinstance(accts_obj, dict) else None
    ) or "/redfish/v1/AccountService/Accounts"
    accts_url = to_abs(c.base_url, str(accts_rel))
    acct_coll, coll_err = c.get_json_maybe(accts_url)
    if coll_err or not acct_coll:
        typer.echo(f"Error: Cannot reach account collection: {coll_err}", err=True)
        raise typer.Exit(1)

    user_list = []
    for member in acct_coll.get("Members", []):
        if not isinstance(member, dict):
            continue
        member_url = to_abs(c.base_url, member.get("@odata.id", ""))
        account, acct_err = c.get_json_maybe(member_url)
        if acct_err or not account:
            continue
        user_list.append(
            {
                "id": account.get("Id"),
                "username": account.get("UserName"),
                "role_id": account.get("RoleId"),
                "enabled": account.get("Enabled"),
                "locked": account.get("Locked"),
            }
        )

    _output(
        {"ok": True, "host": host, "count": len(user_list), "users": user_list}, as_json=json_output
    )


@app.command()
def bios_diff(
    host_a: str = typer.Argument(help="First BMC IP"),
    host_b: str = typer.Argument(help="Second BMC IP"),
    only_diff: bool = typer.Option(True, "--only-diff/--all"),
    key_filter: str | None = typer.Option(None, "--filter", "-f", help="Filter attributes by name"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Compare BIOS settings between two hosts."""
    from redfish_mcp.bios_diff import diff_attributes_smart, get_bios_attributes

    user, password = _creds(host_a)
    c1 = RedfishClient(
        host=host_a, user=user, password=password, verify_tls=verify_tls, timeout_s=timeout
    )
    c2 = RedfishClient(
        host=host_b, user=user, password=password, verify_tls=verify_tls, timeout_s=timeout
    )
    ep1 = c1.discover_system()
    ep2 = c2.discover_system()

    attrs_a, _url_a, err_a = get_bios_attributes(c1, ep1)
    attrs_b, _url_b, err_b = get_bios_attributes(c2, ep2)

    if err_a or err_b or not attrs_a or not attrs_b:
        typer.echo(f"Error: Could not get BIOS from both hosts. A: {err_a}, B: {err_b}", err=True)
        raise typer.Exit(1)

    diff = diff_attributes_smart(attrs_a, attrs_b, keys_like=key_filter)
    if only_diff:
        diff["matched"] = [m for m in diff["matched"] if not m["values_match"]]

    result = {"ok": True, "host_a": host_a, "host_b": host_b, "diff": diff}
    _output(result, as_json=json_output)


def _resolve_oob_ip(hostname: str) -> tuple[str, str, str]:
    """Resolve hostname to OOB IP via netbox-cli.

    Returns (oob_ip, device_name, site_name).
    Raises typer.Exit(1) on failure.
    """
    import json as json_mod
    import subprocess

    try:
        result = subprocess.run(
            ["netbox-cli", "lookup", hostname, "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            typer.echo(f"Error: netbox-cli lookup failed: {result.stderr.strip()}", err=True)
            raise typer.Exit(1)

        data = json_mod.loads(result.stdout)
        results = data.get("results", [])
        if not results:
            typer.echo(f"Error: No device found for '{hostname}' in NetBox", err=True)
            raise typer.Exit(1)

        device = results[0]
        oob_ip = device.get("oob_ip_address") or ""
        if not oob_ip:
            oob_obj = device.get("oob_ip", {})
            if isinstance(oob_obj, dict):
                addr = oob_obj.get("address", "")
                oob_ip = addr.split("/")[0] if addr else ""

        if not oob_ip:
            typer.echo(f"Error: No OOB IP found for '{hostname}' in NetBox", err=True)
            raise typer.Exit(1)

        device_name = device.get("name", hostname)
        site_name = ""
        site_obj = device.get("site", {})
        if isinstance(site_obj, dict):
            site_name = site_obj.get("name", "")

        return oob_ip, device_name, site_name

    except typer.Exit:
        raise
    except subprocess.TimeoutExpired:
        typer.echo("Error: netbox-cli timed out", err=True)
        raise typer.Exit(1) from None
    except json_mod.JSONDecodeError:
        typer.echo("Error: Could not parse netbox-cli output as JSON", err=True)
        raise typer.Exit(1) from None
    except FileNotFoundError:
        typer.echo("Error: netbox-cli not found on PATH. Install it first.", err=True)
        raise typer.Exit(1) from None


def _do_screenshot(
    host: str,
    output: str = "screenshot.jpg",
    method: str = "auto",
    text_only: bool = False,
    ocr: bool = False,
    analyze: str = "",
    verify_tls: bool = False,
    timeout: int = 30,
    analysis_timeout: int | None = None,
) -> None:
    """Core screenshot logic shared by 'screenshot' and 'screenshot-by-name'."""
    from redfish_mcp.screen_capture import (
        capture_screen_cgi,
        capture_screen_dell,
        capture_screen_redfish,
        detect_vendor,
        is_screenshot_supported,
        vendor_from_manufacturer,
        vendor_from_model,
        vendor_methods,
    )

    valid_methods = ("auto", "redfish", "cgi", "dell", "ami")
    if method not in valid_methods:
        typer.echo(f"Error: invalid method '{method}'; use {', '.join(valid_methods)}", err=True)
        raise typer.Exit(1)

    user, password = _creds(host)

    model_vendor_hint: str | None = None
    try:
        c = _client(host, verify_tls, timeout)
        ep = c.discover_system()
        system = c.get_json(ep.system_url)
        if system.get("PowerState", "").lower() == "off":
            typer.echo(
                "Error: host is powered off (PowerState=Off). Power on the system first.", err=True
            )
            raise typer.Exit(1)
        model_vendor_hint = (
            vendor_from_model(system.get("Model", ""))
            or vendor_from_manufacturer(system.get("Manufacturer", ""))
        )
    except typer.Exit:
        raise
    except Exception:
        pass

    vendor = "unknown"
    if method == "auto":
        if model_vendor_hint and model_vendor_hint != "unknown":
            vendor = model_vendor_hint
        else:
            try:
                c = _client(host, verify_tls, timeout)
                vendor = detect_vendor(c)
            except Exception:
                pass
        methods_to_try = vendor_methods(vendor)
    else:
        methods_to_try = [method]

    img_bytes: bytes | None = None
    mime_type: str = "image/jpeg"
    method_used: str = method
    errors: list[str] = []

    for try_method in methods_to_try:
        try:
            if try_method == "redfish":
                c = _client(host, verify_tls, timeout)
                img_bytes, mime_type = capture_screen_redfish(c)
            elif try_method == "cgi":
                img_bytes, mime_type = capture_screen_cgi(host, user, password, verify_tls, timeout)
            elif try_method == "dell":
                img_bytes, mime_type = capture_screen_dell(
                    host, user, password, verify_tls, timeout
                )
            elif try_method == "ami":
                import asyncio

                from redfish_mcp.kvm.backends.playwright_ami import capture_screen_ami

                img_bytes, mime_type = asyncio.run(
                    capture_screen_ami(host, user, password, timeout_s=timeout)
                )
            method_used = try_method
            break
        except Exception as e:
            errors.append(f"{try_method}: {e}")
            if method != "auto":
                typer.echo(f"Error: {try_method.title()} capture failed: {e}", err=True)
                raise typer.Exit(1) from None

    if img_bytes is None:
        if not is_screenshot_supported(vendor):
            typer.echo(
                f"Error: Screenshot capture is not supported for this BMC (detected vendor: {vendor})",
                err=True,
            )
            raise typer.Exit(1)
        detail = "; ".join(errors) if errors else "no capture methods matched"
        vendor_info = f" (detected vendor: {vendor})" if vendor != "unknown" else ""
        typer.echo(f"Error: All screenshot methods failed{vendor_info} — {detail}", err=True)
        raise typer.Exit(1)

    if analyze:
        from redfish_mcp.screen_analysis import analyze_screenshot as run_analysis

        valid_modes = ("summary", "analysis", "diagnosis")
        if analyze not in valid_modes:
            typer.echo(
                f"Error: invalid --analyze mode '{analyze}'; use {', '.join(valid_modes)}", err=True
            )
            raise typer.Exit(1)
        typer.echo(f"  Analyzing ({analyze})...")
        try:
            result = run_analysis(img_bytes, mime_type, analyze, timeout_s=analysis_timeout)
        except _ReadTimeout:
            typer.echo(
                f"Error: Analysis timed out. Try increasing --analysis-timeout "
                f"(current: {analysis_timeout or 'auto'})",
                err=True,
            )
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error: Analysis failed: {e}", err=True)
            raise typer.Exit(1) from None
        typer.echo(
            json.dumps(
                {"ok": True, "host": host, "method": method_used, "screen": result},
                indent=2,
                default=str,
            )
        )
        return

    if text_only or ocr:
        from redfish_mcp.vision import extract_text_from_screenshot

        typer.echo("  Extracting text via OCR...")
        try:
            ocr_text = extract_text_from_screenshot(img_bytes, mime_type)
        except Exception as e:
            typer.echo(f"Error: OCR failed: {e}", err=True)
            raise typer.Exit(1) from None

        if text_only:
            txt_path = output.rsplit(".", 1)[0] + ".txt"
            with open(txt_path, "w") as f:
                f.write(ocr_text)
            typer.echo(f"  host: {host}")
            typer.echo(f"  method: {method_used}")
            typer.echo(f"  ocr_saved: {txt_path}")
            typer.echo(f"  ocr_chars: {len(ocr_text)}")
            return

        if ocr:
            txt_path = output.rsplit(".", 1)[0] + ".txt"
            with open(txt_path, "w") as f:
                f.write(ocr_text)
            typer.echo(f"  ocr_saved: {txt_path}")

    with open(output, "wb") as f:
        f.write(img_bytes)

    typer.echo(f"  host: {host}")
    typer.echo(f"  method: {method_used}")
    typer.echo(f"  size: {len(img_bytes)} bytes")
    typer.echo(f"  saved: {output}")


@app.command()
def screenshot(
    host: str = typer.Argument(help="BMC IP or hostname (use oob_ip from NetBox)"),
    output: str = typer.Option("screenshot.jpg", "--output", "-o", help="Output file path"),
    method: str = typer.Option(
        "auto", "--method", "-m", help="Capture method: auto, redfish, cgi, dell"
    ),
    text_only: bool = typer.Option(
        False, "--text-only", "-t", help="Extract text via OCR instead of saving image"
    ),
    ocr: bool = typer.Option(
        False, "--ocr", help="Also extract text via OCR (saved to .txt alongside image)"
    ),
    analyze: str = typer.Option(
        "", "--analyze", "-a", help="LLM analysis mode: summary, analysis, diagnosis"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    analysis_timeout: int | None = typer.Option(
        None,
        "--analysis-timeout",
        help="Together API timeout for --analyze (default: auto per mode — 90/120/180s)",
    ),
):
    """Capture VGA framebuffer screenshot from BMC.

    Saves JPEG to disk by default. Use --text-only to extract screen text
    via Together AI vision model (OCR), or --ocr to get both.
    Use --analyze for structured LLM analysis (summary/analysis/diagnosis).
    """
    _do_screenshot(
        host=host,
        output=output,
        method=method,
        text_only=text_only,
        ocr=ocr,
        analyze=analyze,
        verify_tls=verify_tls,
        timeout=timeout,
        analysis_timeout=analysis_timeout,
    )


@app.command(name="screenshot-by-name")
def screenshot_by_name(
    hostname: str = typer.Argument(help="Device hostname (e.g. research-common-h100-097)"),
    output: str = typer.Option("screenshot.jpg", "--output", "-o", help="Output file path"),
    method: str = typer.Option(
        "auto", "--method", "-m", help="Capture method: auto, redfish, cgi, dell"
    ),
    text_only: bool = typer.Option(False, "--text-only", "-t", help="Extract text via OCR"),
    ocr: bool = typer.Option(False, "--ocr", help="Also extract text via OCR"),
    analyze: str = typer.Option(
        "", "--analyze", "-a", help="LLM analysis: summary, analysis, diagnosis"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    analysis_timeout: int | None = typer.Option(
        None,
        "--analysis-timeout",
        help="Together API timeout for --analyze (default: auto per mode — 90/120/180s)",
    ),
):
    """Capture VGA screenshot by hostname (resolves OOB IP from NetBox automatically)."""
    oob_ip, device_name, site_name = _resolve_oob_ip(hostname)
    typer.echo(f"Resolved {device_name} -> oob_ip={oob_ip} (site={site_name})")

    _do_screenshot(
        host=oob_ip,
        output=output,
        method=method,
        text_only=text_only,
        ocr=ocr,
        analyze=analyze,
        verify_tls=verify_tls,
        timeout=timeout,
        analysis_timeout=analysis_timeout,
    )


@app.command()
def video(
    host: str = typer.Argument(help="BMC IP or hostname (use oob_ip from NetBox)"),
    output: str = typer.Option("video_capture.bin", "--output", "-o", help="Output file path"),
    dump_type: str = typer.Option(
        "VideoCapture", "--type", "-t", help="VideoCapture or CrashScreenCapture"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
):
    """Download BMC video or crash capture. Saves to disk."""
    from redfish_mcp.screen_capture import download_dump_redfish

    c = _client(host, verify_tls, timeout)
    try:
        data, content_type = download_dump_redfish(c, dump_type)  # type: ignore[arg-type]
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    with open(output, "wb") as f:
        f.write(data)

    typer.echo(f"  host: {host}")
    typer.echo(f"  type: {dump_type}")
    typer.echo(f"  content_type: {content_type}")
    typer.echo(f"  size: {len(data)} bytes")
    typer.echo(f"  saved: {output}")


@app.command()
def watch(
    host: str = typer.Argument(help="BMC IP or hostname (use oob_ip from NetBox)"),
    interval: int = typer.Option(5, "--interval", "-i", help="Seconds between captures (min 2)"),
    count: int = typer.Option(12, "--count", "-n", help="Max captures (max 60)"),
    method: str = typer.Option(
        "auto", "--method", "-m", help="Capture method: auto, redfish, cgi, dell"
    ),
    analyze: str = typer.Option(
        "", "--analyze", "-a", help="LLM analysis: summary, analysis, diagnosis"
    ),
    stop_when: str = typer.Option(
        "", "--stop-when", "-s", help="Stop on: login_prompt, error, interactive, stable"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    analysis_timeout: int | None = typer.Option(
        None,
        "--analysis-timeout",
        help="Together API timeout for --analyze (default: auto per mode — 90/120/180s)",
    ),
):
    """Watch BMC screen by polling screenshots. Shows OCR text or LLM analysis on changes."""
    import hashlib
    import time as _time

    from redfish_mcp.screen_capture import try_capture

    if analyze and analyze not in ("summary", "analysis", "diagnosis"):
        typer.echo(
            f"Error: invalid --analyze mode '{analyze}'; use summary, analysis, diagnosis", err=True
        )
        raise typer.Exit(1)
    if stop_when and stop_when not in ("login_prompt", "error", "interactive", "stable"):
        typer.echo(
            f"Error: invalid --stop-when '{stop_when}'; use login_prompt, error, interactive, stable",
            err=True,
        )
        raise typer.Exit(1)
    if stop_when in ("login_prompt", "error", "interactive") and not analyze:
        typer.echo(
            f"Error: --stop-when={stop_when} requires --analyze (needs LLM to detect screen state)",
            err=True,
        )
        raise typer.Exit(1)

    user, password = _creds(host)
    interval = max(interval, 2)
    count = min(max(count, 1), 60)
    last_hash = None
    stable_count = 0

    for i in range(count):
        if i > 0:
            _time.sleep(interval)

        try:
            img_bytes, mime_type, _ = try_capture(
                host,
                user,
                password,
                method=method,
                verify_tls=verify_tls,
                timeout_s=timeout,
            )
        except RuntimeError:
            typer.echo(f"[{i}] capture failed")
            continue

        frame_hash = hashlib.sha256(img_bytes).hexdigest()[:16]
        if frame_hash == last_hash:
            stable_count += 1
            typer.echo(f"[{i}] no change ({frame_hash})")
            if stop_when == "stable" and stable_count >= 3:
                typer.echo("  Stopped: screen stable")
                break
            continue

        stable_count = 0
        last_hash = frame_hash
        typer.echo(f"[{i}] CHANGED ({frame_hash})")

        if analyze:
            from redfish_mcp.screen_analysis import analyze_screenshot as run_analysis

            try:
                result = run_analysis(img_bytes, mime_type, analyze, timeout_s=analysis_timeout)
                typer.echo(f"    {json.dumps(result, default=str)}")
                if stop_when == "login_prompt" and result.get("screen_type") == "login_prompt":
                    typer.echo("  Stopped: login_prompt detected")
                    break
                if stop_when == "error" and result.get("needs_attention"):
                    typer.echo("  Stopped: error detected")
                    break
                if stop_when == "interactive" and result.get("is_interactive"):
                    typer.echo("  Stopped: interactive screen")
                    break
            except Exception as e:
                typer.echo(f"    Analysis failed: {e}")
        else:
            from redfish_mcp.vision import extract_text_from_screenshot

            try:
                text = extract_text_from_screenshot(img_bytes, mime_type)
                for line in text.strip().split("\n"):
                    typer.echo(f"    {line}")
            except Exception as e:
                typer.echo(f"    OCR failed: {e}")


@app.command()
def power(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Show power supply status, consumption, and voltage readings."""
    from redfish_mcp.chassis_telemetry import collect_power_info

    c = _client(host, verify_tls, timeout)
    result = collect_power_info(c)
    _output({"ok": True, "host": host, **result}, as_json=json_output)


@app.command()
def processors(
    host: str = typer.Argument(help="BMC IP or hostname"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List installed CPUs with model, cores, threads, and speed."""
    from redfish_mcp.system_inventory import collect_processor_inventory

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    result = collect_processor_inventory(c, ep)
    _output({"ok": True, "host": host, **result}, as_json=json_output)


@app.command()
def memory(
    host: str = typer.Argument(help="BMC IP or hostname"),
    show_empty: bool = typer.Option(False, "--show-empty", help="Include empty DIMM slots"),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List memory DIMMs with capacity, speed, type, and health."""
    from redfish_mcp.system_inventory import collect_memory_inventory

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    result = collect_memory_inventory(c, ep)

    if not show_empty:
        result["dimms"] = [d for d in result["dimms"] if d.get("populated", True)]

    _output({"ok": True, "host": host, **result}, as_json=json_output)


@app.command()
def pcie(
    host: str = typer.Argument(help="BMC IP or hostname"),
    category: str | None = typer.Option(
        None, "--category", "-c", help="Filter: gpu, network, storage, pcie_infrastructure"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List PCIe devices (GPUs, NICs, NVMe, switches)."""
    from redfish_mcp.system_inventory import collect_pcie_inventory

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    result = collect_pcie_inventory(c, ep)

    if category:
        result["devices"] = [d for d in result["devices"] if d.get("category") == category]
        result["count"] = len(result["devices"])

    _output({"ok": True, "host": host, **result}, as_json=json_output)


@app.command()
def manager(
    host: str = typer.Argument(help="BMC IP or hostname"),
    include_ethernet: bool = typer.Option(
        False, "--ethernet", "-e", help="Include BMC network interfaces"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Show BMC/Manager details, firmware, and network services."""
    from redfish_mcp.manager_info import collect_manager_ethernet, collect_manager_info

    c = _client(host, verify_tls, timeout)
    result = collect_manager_info(c)
    out: dict = {"ok": True, "host": host, **result}

    if include_ethernet:
        eth = collect_manager_ethernet(c)
        out["ethernet_interfaces"] = eth

    _output(out, as_json=json_output)


@app.command("power-control")
def power_control(
    host: str = typer.Argument(help="BMC IP or hostname"),
    action: str = typer.Argument(
        help=(
            "on, off, force_off, restart, force_restart, nmi (snake_case). "
            "Redfish PascalCase aliases (ForceRestart, ForceOff, GracefulRestart, "
            "GracefulShutdown) are accepted and normalized."
        ),
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Control server power state (on, off, force_off, restart, force_restart, nmi).

    The ``action`` argument uses snake_case; Redfish PascalCase ``ResetType``
    values (e.g. ``ForceRestart``) are accepted as aliases and normalized.
    """
    try:
        canonical, reset_type = resolve_reset_type(action)
    except InvalidActionError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    system = c.get_json(ep.system_url)
    current_power = system.get("PowerState", "Unknown")

    resp = c.post_json(ep.reset_url, {"ResetType": reset_type})
    if resp.status_code >= 400:
        result = {
            "ok": False,
            "error": f"Power control failed: {resp.text[:500]}",
            "host": host,
            "action": canonical,
            "reset_type": reset_type,
            "prior_power_state": current_power,
        }
    else:
        result = {
            "ok": True,
            "host": host,
            "action": canonical,
            "reset_type": reset_type,
            "prior_power_state": current_power,
        }

    _output(result, as_json=json_output)


@app.command("fixed-boot-order")
def fixed_boot_order(
    host: str = typer.Argument(help="BMC IP or hostname"),
    set_order: str | None = typer.Option(
        None,
        "--set",
        "-s",
        help="JSON file path or inline JSON string to PATCH as new boot order",
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Get or set Supermicro persistent UEFI fixed boot order (OEM endpoint).

    Without --set: displays the current boot order.
    With --set: PATCHes a new boot order (requires confirmation, system reset to apply).
    Only available on Supermicro BMCs.
    """
    from pathlib import Path

    from redfish_mcp.supermicro_boot_order import (
        get_fixed_boot_order,
        is_supermicro,
        set_fixed_boot_order,
    )

    c = _client(host, verify_tls, timeout)

    if not is_supermicro(c):
        typer.echo("Error: This BMC is not Supermicro (OEM endpoint not found)", err=True)
        raise typer.Exit(1)

    if set_order is None:
        data, _etag, err = get_fixed_boot_order(c)
        if err:
            typer.echo(f"Error: {err}", err=True)
            raise typer.Exit(1)
        _output({"ok": True, "host": host, "fixed_boot_order": data}, as_json=json_output)
        return

    if Path(set_order).is_file():
        payload = json.loads(Path(set_order).read_text())
    else:
        try:
            payload = json.loads(set_order)
        except json.JSONDecodeError as e:
            typer.echo(f"Error: Invalid JSON: {e}", err=True)
            raise typer.Exit(1) from None

    typer.echo(f"About to PATCH FixedBootOrder on {host}")
    typer.echo(f"Payload: {json.dumps(payload, indent=2)}")
    if not typer.confirm("Proceed? (write operation)"):
        raise typer.Abort()

    result = set_fixed_boot_order(c, payload)
    _output({"host": host, **result}, as_json=json_output)


@app.command("set-boot")
def set_boot(
    host: str = typer.Argument(help="BMC IP or hostname (use oob_ip from NetBox)"),
    target: str = typer.Option(
        ...,
        "--target",
        "-t",
        help="Boot target (e.g. Pxe, Hdd, BiosSetup, Cd, None). Validated against AllowableValues.",
    ),
    enabled: str = typer.Option(
        "Once",
        "--enabled",
        "-e",
        help="Override mode: Once, Continuous, or Disabled",
    ),
    reboot: bool = typer.Option(False, "--reboot", "-r", help="Reboot after setting boot override"),
    reset_type: str = typer.Option(
        "GracefulRestart",
        "--reset-type",
        help="Redfish ResetType for --reboot (default: GracefulRestart)",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt (write operation)"
    ),
    verify_tls: bool = typer.Option(False, "--verify-tls"),
    timeout: int = typer.Option(30, "--timeout"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Set BootSourceOverride (standard Redfish, works on all vendors).

    Discovers the system member dynamically, validates the target against
    the BMC's AllowableValues, and PATCHes the Boot object. Optionally
    triggers a reboot with --reboot.

    Requires --yes to confirm (write operation).

    Examples:
        redfish-cli set-boot 10.0.0.1 --target Pxe --yes
        redfish-cli set-boot 10.0.0.1 --target BiosSetup --enabled Once --reboot --yes
        redfish-cli set-boot 10.0.0.1 --target Hdd --enabled Continuous --yes --json
    """
    from redfish_mcp.boot import get_allowable_targets, pick_target

    valid_enabled = ("Once", "Continuous", "Disabled")
    if enabled not in valid_enabled:
        typer.echo(f"Error: --enabled must be one of {', '.join(valid_enabled)}", err=True)
        raise typer.Exit(1)

    c = _client(host, verify_tls, timeout)
    ep = c.discover_system()
    system = c.get_json(ep.system_url)

    allowable = get_allowable_targets(system)
    chosen_target, attempted = pick_target(target, allowable)

    current_boot = system.get("Boot") or {}
    current_mode = current_boot.get("BootSourceOverrideMode")

    if not yes:
        typer.echo(f"About to set BootSourceOverride on {host}")
        typer.echo(f"  system_url: {ep.system_url}")
        typer.echo(f"  target: {chosen_target} (requested: {target})")
        typer.echo(f"  enabled: {enabled}")
        if allowable:
            typer.echo(f"  allowable: {', '.join(allowable)}")
        if reboot:
            typer.echo(f"  reboot: {reset_type}")
        if not typer.confirm("Proceed? (write operation)"):
            raise typer.Abort()

    payload_boot: dict[str, Any] = {
        "BootSourceOverrideEnabled": enabled,
        "BootSourceOverrideTarget": chosen_target,
    }
    if isinstance(current_mode, str) and current_mode:
        payload_boot["BootSourceOverrideMode"] = current_mode
    payload = {"Boot": payload_boot}

    resp = c.patch_json(ep.system_url, payload)
    if resp.status_code >= 400:
        result: dict[str, Any] = {
            "ok": False,
            "error": f"PATCH failed: {resp.status_code}",
            "detail": resp.text[:2000],
            "host": host,
            "system_url": ep.system_url,
            "payload": payload,
        }
        _output(result, as_json=json_output)
        raise typer.Exit(1)

    result = {
        "ok": True,
        "host": host,
        "system_url": ep.system_url,
        "chosen_target": chosen_target,
        "enabled": enabled,
        "attempted_targets": attempted,
        "http_status": resp.status_code,
    }

    if reboot:
        post = c.post_json(ep.reset_url, {"ResetType": reset_type})
        if post.status_code >= 400:
            result["reboot_ok"] = False
            result["reboot_error"] = post.text[:2000]
        else:
            result["reboot_ok"] = True
            result["reset_type"] = reset_type

    _output(result, as_json=json_output)


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    try:
        from mcp_common.logging import setup_logging

        setup_logging(name="redfish-cli", level="WARNING")
    except Exception:
        pass

    app()


if __name__ == "__main__":
    main()
