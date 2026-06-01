"""
maas-cli: Thin CLI wrapper around the MAAS REST API.

Provides the same capabilities as maas-mcp but via shell commands,
enabling AI agents to use MAAS with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

import typer
from mcp_common.agent_remediation import (
    format_agent_exception_remediation,
    install_cli_exception_handler,
)
from redfish_mcp.hints import HINTS as REDFISH_HINTS

from maas_mcp.bond_audit import (
    build_audit_result,
    build_summary,
    extract_maas_bond_config,
    resolve_cluster_hostnames,
    resolve_maas_hostnames,
    ssh_bond_info,
)
from maas_mcp.config import Settings, _discover_prefixed_instances, _ensure_scheme
from maas_mcp.maas_client import MaasRestClient, is_maas_http_error
from maas_mcp.netbox_resolve import (
    NetboxResolveResult,
    format_netbox_resolution_hint,
    resolve_netbox_device_to_maas_system_id,
)
from maas_mcp.node_status import apply_status_coercion_to_machine_params

app = typer.Typer(
    name="maas-cli",
    help="Query and manage MAAS bare-metal servers. Use --help on any subcommand for details.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/maas-mcp")


def _build_clients(site: str | None = None) -> dict[str, MaasRestClient]:
    """Build MAAS clients from env vars. Returns {name: client} dict."""
    verify = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
    timeout = float(os.environ.get("MAAS_TIMEOUT", "30"))
    clients: dict[str, MaasRestClient] = {}

    for name, cfg in _discover_prefixed_instances().items():
        clients[name] = MaasRestClient(
            url=_ensure_scheme(cfg["url"]),
            api_key=cfg["api_key"],
            verify_ssl=verify,
            timeout_seconds=timeout,
        )

    url = os.environ.get("MAAS_URL")
    api_key = os.environ.get("MAAS_API_KEY")
    if url and api_key:
        clients["default"] = MaasRestClient(
            url=_ensure_scheme(url),
            api_key=api_key,
            verify_ssl=verify,
            timeout_seconds=timeout,
        )

    instances_json = os.environ.get("MAAS_INSTANCES")
    if instances_json:
        try:
            for name, cfg in json.loads(instances_json).items():
                clients[name] = MaasRestClient(
                    url=_ensure_scheme(cfg["url"]),
                    api_key=cfg["api_key"],
                    verify_ssl=verify,
                    timeout_seconds=timeout,
                )
        except (json.JSONDecodeError, KeyError) as e:
            typer.echo(f"Warning: MAAS_INSTANCES parse error: {e}", err=True)

    if not clients:
        typer.echo(
            "Error: No MAAS instances configured. Set MAAS_URL+MAAS_API_KEY "
            "or MAAS_{SITE}_URL+MAAS_{SITE}_API_KEY env vars.",
            err=True,
        )
        raise typer.Exit(1)

    if site:
        s = site.lower()
        if s not in clients:
            typer.echo(
                f"Error: site '{site}' not found. Available: {', '.join(sorted(clients))}",
                err=True,
            )
            raise typer.Exit(1)
        return {s: clients[s]}

    return clients


def _get_client(site: str | None) -> tuple[str, MaasRestClient]:
    """Return (name, client) for the requested site, or the first available."""
    clients = _build_clients(site)
    name = next(iter(clients))
    return name, clients[name]


class _PrintContext:
    """Minimal logging context so MCP tool coroutines can run under Typer."""

    async def info(self, msg: str) -> None:
        typer.echo(f"[info] {msg}")

    async def warning(self, msg: str) -> None:
        typer.echo(f"[warn] {msg}", err=True)

    async def error(self, msg: str) -> None:
        typer.echo(f"[err] {msg}", err=True)

    async def debug(self, msg: str) -> None:
        pass

    async def report_progress(self, **_kw: Any) -> None:
        pass

    async def elicit(self, *_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("elicitation unsupported in CLI")


def _extract_tool_result(result: Any) -> Any:
    """Extract structured content from a ToolResult (or pass through dicts)."""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "content"):
        try:
            return json.loads(str(result.content))
        except json.JSONDecodeError:
            return {"content": result.content}
    return result


def _maas_mcp_version() -> str | None:
    try:
        return pkg_version("maas-mcp")
    except PackageNotFoundError:
        return None


def _handle_404(
    exc: RuntimeError,
    *,
    resource: str,
    identifier: str,
    command: str,
    netbox: NetboxResolveResult | None = None,
) -> None:
    """Print a user-friendly 404 message with remediation hints and exit 1."""
    typer.echo(
        f"Error: {resource} not found for {identifier!r} "
        "(MAAS returned 404 — check system_id, site, or NetBox vs MAAS naming).",
        err=True,
    )
    typer.echo(
        "Hint: MAAS hostnames (e.g. gpu001) differ from NetBox tenant names "
        "(e.g. research-common-h100-001). Use NetBox custom_fields.Provider_Machine_ID "
        "to find the correct MAAS identifier.",
        err=True,
    )
    if netbox is not None:
        nh = format_netbox_resolution_hint(netbox)
        if nh:
            typer.echo(nh, err=True)
    try:
        settings = Settings()
        has_netbox = bool(settings.netbox_url and settings.netbox_token)
    except Exception:
        has_netbox = False
    if not has_netbox:
        typer.echo(
            "Tip: Set NETBOX_URL and NETBOX_TOKEN to resolve NetBox device names to MAAS "
            "system_id via custom_fields.Provider_Machine_ID.",
            err=True,
        )

    remediation = format_agent_exception_remediation(
        exception=exc,
        project_repo="vhspace/maas-mcp",
        issue_tracker_url=None,
        tool_or_command=f"maas-cli {command}",
        version=_maas_mcp_version(),
        extra_lines=[f"{resource}={identifier!r}"],
    )
    typer.echo(remediation.rstrip(), err=True)
    raise typer.Exit(1) from None


def _get_or_404(
    client: MaasRestClient,
    path: str,
    *,
    resource: str,
    identifier: str,
    command: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET path; on MAAS 404 print CLI-friendly error and exit."""
    try:
        return client.get(path, params=params)
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            _handle_404(e, resource=resource, identifier=identifier, command=command)
        raise


def _get_machine_for_poll(
    client: MaasRestClient,
    system_id: str,
    *,
    command: str,
) -> dict[str, Any]:
    """Fetch machine during op/wait polling; exit cleanly on unexpected 404."""
    try:
        m = client.get(f"machines/{system_id}")
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            _handle_404(e, resource="system_id", identifier=system_id, command=command)
        raise
    return m if isinstance(m, dict) else {}


def _resolve_via_netbox(identifier: str, client: MaasRestClient) -> NetboxResolveResult:
    """Resolve NetBox device name to MAAS system_id (structured outcome for stderr hints)."""
    return resolve_netbox_device_to_maas_system_id(
        identifier,
        client,
        on_resolved=lambda i, h, s: typer.echo(
            f"  Resolved NetBox device {i!r} -> MAAS hostname {h!r} (system_id: {s})",
            err=True,
        ),
    )


def _normalize_list(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and "results" in response:
        return list(response["results"])
    return [response] if response is not None else []


def _format_machine_line(m: dict, detail: bool = False) -> str:
    """Compact one-line summary of a machine."""
    hostname = m.get("hostname", "?")
    sid = m.get("system_id", "?")
    status = m.get("status_name", "?")
    power = m.get("power_state", "?")
    zone = m.get("zone", {})
    zone_name = zone.get("name", zone) if isinstance(zone, dict) else zone
    pool = m.get("pool", {})
    pool_name = pool.get("name", pool) if isinstance(pool, dict) else pool
    cpus = m.get("cpu_count", "")
    mem = m.get("memory", "")

    parts = [f"{hostname} ({sid})", f"status={status}", f"power={power}"]
    if zone_name:
        parts.append(f"zone={zone_name}")
    if pool_name:
        parts.append(f"pool={pool_name}")
    if cpus:
        parts.append(f"cpus={cpus}")
    if mem:
        mem_gb = round(int(mem) / 1024, 1) if str(mem).isdigit() else mem
        parts.append(f"mem={mem_gb}G")

    if detail:
        ifaces = m.get("interface_set", [])
        bonds = [i.get("name") for i in ifaces if isinstance(i, dict) and i.get("type") == "bond"]
        if bonds:
            parts.append(f"bonds={','.join(bonds)}")
        else:
            parts.append("bonds=none")

        disks = [
            d
            for d in m.get("blockdevice_set", m.get("physicalblockdevice_set", []))
            if isinstance(d, dict)
        ]
        parts.append(f"disks={len(disks)}")
        total_tb = sum(d.get("size", 0) for d in disks) / (1024**4)
        if total_tb > 0:
            parts.append(f"storage={total_tb:.1f}T")

    return "  ".join(parts)


def _output(data: object, as_json: bool = False, detail: bool = False) -> None:
    """Print output — compact text by default, JSON with --json."""
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, list):
        typer.echo(f"# {len(data)} result(s)")
        for item in data:
            if isinstance(item, dict) and "hostname" in item and "system_id" in item:
                typer.echo(_format_machine_line(item, detail=detail))
            elif isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, dict):
                        v = v.get("name", v.get("display", v))
                    typer.echo(f"  {k}: {v}")
                typer.echo()
            else:
                typer.echo(f"  {item}")
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                v = v.get("name", v.get("display", v.get("address", v)))
            elif isinstance(v, list) and len(v) > 5:
                v = f"[{len(v)} items]"
            typer.echo(f"  {k}: {v}")
    else:
        typer.echo(data)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def status(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance name"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Check MAAS connectivity and list configured instances."""
    clients = _build_clients(site)
    result: dict[str, Any] = {}
    for name, client in clients.items():
        try:
            version = client.get_version()
            result[name] = {"url": client.base_url, "version": version, "status": "ok"}
        except Exception as e:
            result[name] = {"url": client.base_url, "status": "error", "error": str(e)}

    if json_output:
        _output(result, as_json=True)
    else:
        for name, info in result.items():
            mark = "ok" if info["status"] == "ok" else "ERROR"
            version = info.get("version", "?")
            typer.echo(f"  {name:<20} {info['url']:<50} v{version}  [{mark}]")


@app.command()
def machines(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    hostname: list[str] | None = typer.Option(
        None, "--hostname", "-H", help="Filter by hostname (repeatable, or comma-separated)"
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        help=(
            "Filter by NodeStatus: integer (0-22), or alias (ready, deployed, ...). "
            "Raw strings like 'Ready' often 400 on MAAS; see README. "
            "Use: maas-cli node-status-values"
        ),
    ),
    zone: str | None = typer.Option(None, "--zone", "-z", help="Filter by zone"),
    pool: str | None = typer.Option(None, "--pool", "-p", help="Filter by pool"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    detail: bool = typer.Option(
        False, "--detail", "-d", help="Show bond interfaces and disk count"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List machines across MAAS instances."""
    clients = _build_clients(site)
    all_machines: list[dict] = []

    hostnames: list[str] = []
    if hostname:
        for h in hostname:
            hostnames.extend(part.strip() for part in h.split(",") if part.strip())

    params: dict[str, Any] = {}
    if len(hostnames) == 1:
        params["hostname"] = hostnames[0]
    elif len(hostnames) > 1:
        params["hostname"] = hostnames
    if status_filter:
        params["status"] = status_filter
    if zone:
        params["zone"] = zone
    if pool:
        params["pool"] = pool
    if tag:
        params["tags"] = tag

    params = apply_status_coercion_to_machine_params(params)

    for name, client in clients.items():
        result = _normalize_list(client.get("machines", params=params or None))
        for m in result:
            m["_site"] = name
        all_machines.extend(result)

    field_list = [f.strip() for f in fields.split(",")] if fields else None
    if field_list:
        all_machines = [{k: v for k, v in m.items() if k in field_list} for m in all_machines]

    _output(all_machines, as_json=json_output, detail=detail)


@app.command("node-status-values")
def node_status_values_cmd(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Print MAAS NodeStatus codes for ``maas-cli machines --status``."""
    from maas_mcp.node_status import NODE_STATUS_REFERENCE

    if json_output:
        _output(NODE_STATUS_REFERENCE, as_json=True)
    else:
        typer.echo("# Use integer value or lowercase alias with --status")
        for row in NODE_STATUS_REFERENCE:
            keys = ",".join(row["keys"])
            typer.echo(f"  {row['value']:>2}  {row['status_name']:<34}  {keys}")


@app.command()
def machine(
    system_id: str = typer.Argument(help="Machine system_id"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    include: str | None = typer.Option(
        None,
        "--include",
        "-i",
        help="Extra sections: interfaces,storage,power_parameters,power_state,events,details,volume_groups,raids (comma-sep)",
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get detailed info for a single machine by system_id.

    NOTE: MAAS hostnames (e.g. gpu001) differ from NetBox tenant names
    (e.g. research-common-h100-001).  Use NetBox custom_fields.Provider_Machine_ID
    to map between the two naming schemes.
    """
    _, client = _get_client(site)
    try:
        result = client.get(f"machines/{system_id}")
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            nb_res = _resolve_via_netbox(system_id, client)
            if nb_res.system_id:
                system_id = nb_res.system_id
                result = client.get(f"machines/{system_id}")
            else:
                _handle_404(
                    e,
                    resource="system_id",
                    identifier=system_id,
                    command="machine",
                    netbox=nb_res,
                )
        else:
            raise

    sections = {s.strip().lower() for s in include.split(",")} if include else set()

    if "interfaces" in sections:
        ifaces = _normalize_list(
            _get_or_404(
                client,
                f"nodes/{system_id}/interfaces",
                resource="interfaces",
                identifier=system_id,
                command="machine",
            )
        )
        result["interfaces"] = ifaces

    if "storage" in sections:
        devs = _normalize_list(
            _get_or_404(
                client,
                f"nodes/{system_id}/blockdevices",
                resource="storage",
                identifier=system_id,
                command="machine",
            )
        )
        result["block_devices"] = devs

    if "details" in sections:
        result["details"] = client.get_safe(f"machines/{system_id}", params={"op": "details"})

    if "volume_groups" in sections:
        result["volume_groups"] = _normalize_list(
            _get_or_404(
                client,
                f"nodes/{system_id}/volume-groups",
                resource="volume_groups",
                identifier=system_id,
                command="machine",
            )
        )

    if "raids" in sections:
        result["raids"] = _normalize_list(
            _get_or_404(
                client,
                f"nodes/{system_id}/raids",
                resource="raids",
                identifier=system_id,
                command="machine",
            )
        )

    if "power_parameters" in sections:
        power = _get_or_404(
            client,
            f"machines/{system_id}",
            resource="power_parameters",
            identifier=system_id,
            command="machine",
            params={"op": "power_parameters"},
        )
        if isinstance(power, dict) and "power_pass" in power:
            power = dict(power)
            power["power_pass"] = "***REDACTED***"
        result["power_parameters"] = power

    if "power_state" in sections:
        result["power_state_queried"] = _get_or_404(
            client,
            f"machines/{system_id}",
            resource="power_state",
            identifier=system_id,
            command="machine",
            params={"op": "query_power_state"},
        )

    if "events" in sections:
        hostname = result.get("hostname")
        if hostname:
            result["recent_events"] = client.get(
                "events", params={"op": "query", "limit": 50, "hostname": hostname}
            )

    field_list = [f.strip() for f in fields.split(",")] if fields else None
    if field_list and isinstance(result, dict):
        result = {k: v for k, v in result.items() if k in field_list}

    _output(result, as_json=json_output)


@app.command()
def events(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    hostname: str | None = typer.Option(None, "--hostname", "-H", help="Filter by hostname"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max events to return"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Query the MAAS event stream."""
    _, client = _get_client(site)
    params: dict[str, Any] = {"op": "query", "limit": limit}
    if hostname:
        params["hostname"] = hostname
    result = client.get("events", params=params)

    if json_output:
        _output(result, as_json=True)
    else:
        evts = result.get("events", []) if isinstance(result, dict) else []
        typer.echo(f"# {len(evts)} event(s)")
        for e in evts:
            ts = e.get("created", "")
            etype = e.get("type_name", e.get("type", ""))
            node = e.get("hostname", e.get("node", ""))
            desc = e.get("description", "")
            typer.echo(f"  {ts}  {node:<30}  {etype:<25}  {desc[:80]}")


@app.command()
def network(
    resource_type: str = typer.Argument(
        help="Network resource: zones, fabrics, subnets, vlans, dns_resources, domains, spaces, dns_records, static_routes"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    fabric_id: int | None = typer.Option(None, "--fabric-id", help="Fabric ID (for vlans)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List MAAS network resources."""
    _, client = _get_client(site)
    rt = resource_type.strip().lower()
    valid = (
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
    if rt not in valid:
        typer.echo(f"Error: resource_type must be one of {valid}", err=True)
        raise typer.Exit(1)

    if rt == "vlans" and fabric_id is not None:
        result = _normalize_list(client.get(f"fabrics/{fabric_id}/vlans"))
    elif rt == "vlans":
        fabrics = _normalize_list(client.get("fabrics"))
        result = []
        for f in fabrics:
            fid = f.get("id")
            if fid is not None:
                try:
                    result.extend(_normalize_list(client.get(f"fabrics/{fid}/vlans")))
                except Exception:
                    pass
    elif rt == "dns_resources":
        result = _normalize_list(client.get("dnsresources"))
    elif rt == "domains":
        result = _normalize_list(client.get("domains"))
    elif rt == "spaces":
        result = _normalize_list(client.get("spaces"))
    elif rt == "dns_records":
        result = _normalize_list(client.get("dnsresourcerecords"))
    elif rt == "static_routes":
        result = _normalize_list(client.get("static-routes"))
    else:
        result = _normalize_list(client.get(rt))

    _output(result, as_json=json_output)


@app.command()
def racks(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    hostname: str | None = typer.Option(None, "--hostname", "-H", help="Filter by hostname"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List rack controllers and their service health."""
    _, client = _get_client(site)
    result = _normalize_list(client.get("rackcontrollers"))
    if hostname:
        result = [r for r in result if hostname.lower() in r.get("hostname", "").lower()]

    if json_output:
        _output(result, as_json=True)
    else:
        typer.echo(f"# {len(result)} rack controller(s)")
        for r in result:
            name = r.get("hostname", "?")
            sid = r.get("system_id", "?")
            zone = r.get("zone", {})
            zone_name = zone.get("name", zone) if isinstance(zone, dict) else zone
            typer.echo(f"  {name} ({sid})  zone={zone_name}")
            for svc in r.get("service_set", []):
                svc_name = svc.get("name", "?")
                svc_status = svc.get("status", "?")
                mark = "ok" if svc_status == "running" else svc_status
                typer.echo(f"    {svc_name}: {mark}")


@app.command()
def images(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List boot images and sync status."""
    _, client = _get_client(site)
    resources = _normalize_list(client.get("boot-resources", params={"type": "synced"}))
    is_importing = client.get("boot-resources", params={"op": "is_importing"})

    if json_output:
        _output({"boot_resources": resources, "is_importing": bool(is_importing)}, as_json=True)
    else:
        typer.echo(f"# {len(resources)} boot image(s)  importing={bool(is_importing)}")
        for r in resources:
            name = r.get("name", "?")
            arch = r.get("architecture", "?")
            rtype = r.get("type", "?")
            typer.echo(f"  {name:<45} arch={arch}  type={rtype}")


def _check_boot_resources(
    client: MaasRestClient,
    *,
    osystem: str | None = None,
    series: str | None = None,
) -> dict[str, Any]:
    """Check boot resource availability and sync status via MAAS API.

    Returns a dict with health status, matching resources, and sync state.
    """
    resources = _normalize_list(client.get("boot-resources", params={"type": "synced"}))
    is_importing = bool(client.get("boot-resources", params={"op": "is_importing"}))

    racks = _normalize_list(client.get("rackcontrollers"))
    rack_health: list[dict[str, Any]] = []
    for r in racks:
        svc_set = r.get("service_set", [])
        http_svc = next((s for s in svc_set if s.get("name") == "http"), None)
        rack_health.append(
            {
                "hostname": r.get("hostname", "?"),
                "system_id": r.get("system_id", "?"),
                "http_service": http_svc.get("status", "unknown") if http_svc else "not_found",
            }
        )

    matched: list[dict[str, Any]] = []
    for res in resources:
        name = res.get("name", "")
        # name format: "ubuntu/jammy" or "custom/myimage"
        parts = name.split("/", 1)
        res_os = parts[0] if len(parts) > 0 else ""
        res_series = parts[1] if len(parts) > 1 else ""

        if osystem and res_os != osystem:
            continue
        if series and res_series != series:
            continue
        matched.append(
            {
                "id": res.get("id"),
                "name": name,
                "architecture": res.get("architecture", "?"),
                "size": res.get("size"),
                "complete": res.get("complete", True),
                "last_deployed": res.get("last_deployed"),
            }
        )

    healthy = bool(matched) and not is_importing
    issues: list[str] = []
    if not matched:
        filter_desc = "/".join(filter(None, [osystem, series])) or "any"
        issues.append(f"No synced boot resources match '{filter_desc}'")
    if is_importing:
        issues.append("Boot resource import is currently in progress — cache may be incomplete")
    for rh in rack_health:
        if rh["http_service"] not in ("running", "unknown"):
            issues.append(f"Rack {rh['hostname']}: HTTP service is {rh['http_service']}")

    return {
        "ok": healthy,
        "is_importing": is_importing,
        "matched_resources": matched,
        "total_synced_resources": len(resources),
        "rack_controllers": rack_health,
        "issues": issues,
    }


_NODE_TYPE_MAP = {
    "Region and rack controller": "region+rack",
    "Rack controller": "rack",
    "Region controller": "region",
}

_RACK_CRITICAL_SERVICES = {"rackd", "http", "tftp"}
_REGION_CRITICAL_SERVICES = {"regiond", "proxy", "reverse_proxy", "temporal", "temporal-worker"}
_EXEMPT_SERVICES = {"dhcpd", "dhcpd6", "syslog_rack", "dns_rack"}
_CONDITIONALLY_EXEMPT = {"ntp_rack", "proxy_rack"}


def _build_ssh_hints(hostname: str, ip: str, node_type: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    hints["check_proxy_cache_dir"] = f"ssh {ip} grep cache_dir /var/lib/maas/maas-proxy.conf"
    hints["check_proxy_cache_size"] = (
        f"ssh {ip} du -sh /var/lib/maas/proxy-cache /var/spool/maas-proxy 2>/dev/null"
    )
    hints["check_proxy_template"] = (
        f"ssh {ip} grep 'cache_dir' /usr/lib/python3/dist-packages/provisioningserver"
        f"/templates/proxy/maas-proxy.conf.template"
    )
    hints["check_install_method"] = (
        f"ssh {ip} 'snap list maas 2>/dev/null && echo SNAP || "
        f"(dpkg -l maas-rack-controller 2>/dev/null | grep -q ^ii && echo DEB || echo UNKNOWN)'"
    )
    hints["check_image_storage"] = (
        f"ssh {ip} 'du -sh /var/lib/maas/boot-resources /var/lib/maas/image-storage 2>/dev/null; "
        f"df -h /var/lib/maas 2>/dev/null'"
    )
    hints["check_disk_usage"] = f"ssh {ip} df -h /var/lib/maas /var/spool/maas-proxy"
    hints["check_ip_addresses"] = f"ssh {ip} ip -4 addr show scope global"
    hints["check_systemd_services"] = (
        f"ssh {ip} systemctl status maas-proxy maas-rackd maas-http --no-pager -l"
    )
    hints["check_proxy_logs"] = (
        f"ssh {ip} journalctl -u maas-proxy --no-pager -n 20 --since '1 hour ago'"
    )
    if node_type in ("region", "region+rack"):
        hints["check_regiond_logs"] = (
            f"ssh {ip} journalctl -u maas-regiond --no-pager -n 50 --since '1 hour ago' "
            f"| grep -i 'error\\|fail\\|import\\|boot.resource'"
        )
    return hints


def _is_service_exempt(name: str, status: str, status_info: str) -> bool:
    if name in _EXEMPT_SERVICES:
        return True
    if name in _CONDITIONALLY_EXEMPT and "managed by the region" in (status_info or "").lower():
        return True
    return False


def _classify_controller(
    controller: dict[str, Any],
    critical_services: set[str],
) -> dict[str, Any]:
    hostname = controller.get("hostname", "?")
    system_id = controller.get("system_id", "?")
    raw_type = controller.get("node_type_name", "")
    node_type = _NODE_TYPE_MAP.get(raw_type, raw_type)
    version = controller.get("version", "")
    zone = controller.get("zone", {})
    zone_name = zone.get("name", "?") if isinstance(zone, dict) else str(zone)
    ip_addresses = controller.get("ip_addresses", [])

    services: dict[str, str] = {}
    issues: list[str] = []
    non_exempt_dead = 0
    non_exempt_total = 0

    for svc in controller.get("service_set", []):
        svc_name = svc.get("name", "")
        svc_status = svc.get("status", "unknown")
        svc_info = svc.get("status_info", "") or ""
        services[svc_name] = svc_status

        if _is_service_exempt(svc_name, svc_status, svc_info):
            continue

        non_exempt_total += 1
        if svc_status not in ("running",):
            non_exempt_dead += 1
            if svc_name in critical_services:
                issues.append(f"{svc_name} is {svc_status}")

    if non_exempt_total > 0 and non_exempt_dead == non_exempt_total:
        status = "offline"
    elif issues:
        status = "degraded"
    else:
        status = "healthy"

    ssh_commands: dict[str, str] = {}
    if ip_addresses:
        first_ip = ip_addresses[0] if isinstance(ip_addresses[0], str) else str(ip_addresses[0])
        ssh_commands = _build_ssh_hints(hostname, first_ip, node_type)

    return {
        "hostname": hostname,
        "system_id": system_id,
        "version": version,
        "node_type": node_type,
        "zone": zone_name,
        "status": status,
        "services": services,
        "issues": issues,
        "ip_addresses": ip_addresses,
        "ssh_commands": ssh_commands,
    }


def _check_database(db_url: str | None) -> dict[str, Any]:
    if not db_url:
        return {"ok": True, "skipped": True, "reason": "No MAAS_DB_URL configured"}

    import psycopg

    try:
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.execute("SELECT count(*) FROM maasserver_node")
                node_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM maasserver_bootresourcefile "
                    "WHERE largefile_id IS NOT NULL "
                    "AND updated < now() - interval '24 hours'"
                )
                stale_count = cur.fetchone()[0]
        result: dict[str, Any] = {
            "ok": True,
            "connected": True,
            "node_count": node_count,
            "stale_boot_resource_files": stale_count,
        }
        if stale_count > 0:
            result["ok"] = False
            result["issues"] = [f"{stale_count} stale boot resource file(s) older than 24h"]
        return result
    except Exception as exc:
        return {"ok": False, "connected": False, "error": str(exc)}


def _validate_health(
    client: MaasRestClient,
    *,
    db_url: str | None = None,
    check_images: bool = True,
) -> dict[str, Any]:
    all_issues: list[str] = []

    # --- Category 1: Controller services ---
    racks_raw = _normalize_list(client.get("rackcontrollers"))
    regions_raw = _normalize_list(client.get("regioncontrollers"))

    rack_results = [
        _classify_controller(r, _RACK_CRITICAL_SERVICES | _REGION_CRITICAL_SERVICES)
        for r in racks_raw
    ]
    rack_system_ids = {r.get("system_id") for r in racks_raw}
    region_results = [
        _classify_controller(r, _REGION_CRITICAL_SERVICES)
        for r in regions_raw
        if r.get("system_id") not in rack_system_ids
    ]

    controllers_ok = all(
        c["status"] == "healthy" for c in rack_results + region_results
    )
    for c in rack_results + region_results:
        for issue in c["issues"]:
            all_issues.append(f"{c['hostname']}: {issue}")

    # --- Category 2: Database ---
    db_result = _check_database(db_url)
    if not db_result.get("skipped") and not db_result["ok"]:
        for issue in db_result.get("issues", []):
            all_issues.append(f"database: {issue}")
        if db_result.get("error"):
            all_issues.append(f"database: {db_result['error']}")

    # --- Category 3: Squid proxy cache ---
    proxy_services: list[dict[str, str]] = []
    proxy_issues: list[str] = []
    for c in rack_results + region_results:
        for svc_name in ("proxy", "proxy_rack"):
            st = c["services"].get(svc_name)
            if st is not None:
                proxy_services.append({
                    "controller": c["hostname"],
                    "service": svc_name,
                    "status": st,
                })
                if st not in ("running",) and svc_name == "proxy":
                    proxy_issues.append(f"{c['hostname']}: {svc_name} is {st}")

    http_proxy: str | None = None
    try:
        http_proxy = client.get("maas", params={"op": "get_config", "name": "http_proxy"})
    except Exception:
        pass

    proxy_ok = len(proxy_issues) == 0
    for issue in proxy_issues:
        all_issues.append(f"proxy: {issue}")

    # --- Category 4: Boot image sync ---
    images_result: dict[str, Any]
    if check_images:
        resources = _normalize_list(client.get("boot-resources", params={"type": "synced"}))
        is_importing = bool(client.get("boot-resources", params={"op": "is_importing"}))
        incomplete = [r for r in resources if not r.get("complete", True)]
        img_issues: list[str] = []
        if not resources:
            img_issues.append("No synced boot resources found")
        if is_importing:
            img_issues.append("Boot resource import is in progress")
        if incomplete:
            img_issues.append(f"{len(incomplete)} incomplete boot resource(s)")
        images_result = {
            "ok": len(img_issues) == 0,
            "is_importing": is_importing,
            "synced_count": len(resources),
            "incomplete_count": len(incomplete),
            "issues": img_issues,
        }
        for issue in img_issues:
            all_issues.append(f"images: {issue}")
    else:
        images_result = {"ok": True, "skipped": True}

    # --- Category 5: Version consistency ---
    all_controllers = rack_results + region_results
    seen_version_sids: set[str] = set()
    unique_controllers: list[dict[str, Any]] = []
    for c in all_controllers:
        if c["system_id"] not in seen_version_sids:
            seen_version_sids.add(c["system_id"])
            unique_controllers.append(c)
    versions = [c["version"] for c in unique_controllers if c["version"]]
    version_issues: list[str] = []
    expected_version = ""
    version_details: list[dict[str, str]] = []
    if versions:
        counts = Counter(versions)
        expected_version = counts.most_common(1)[0][0]
        for c in unique_controllers:
            version_details.append({
                "hostname": c["hostname"],
                "version": c["version"],
            })
            if c["version"] and c["version"] != expected_version:
                version_issues.append(
                    f"{c['hostname']} running {c['version']} (expected {expected_version})"
                )
    for issue in version_issues:
        all_issues.append(f"versions: {issue}")

    # --- Category 6: Controller IP/networking ---
    ip_map: dict[str, list[str]] = {}
    no_ip_controllers: list[str] = []
    for c in rack_results:
        ips = c.get("ip_addresses", [])
        if not ips:
            no_ip_controllers.append(c["hostname"])
        for ip in ips:
            ip_str = ip if isinstance(ip, str) else str(ip)
            ip_map.setdefault(ip_str, []).append(c["hostname"])

    duplicate_ips: list[dict[str, Any]] = []
    for ip_addr, hosts in ip_map.items():
        if len(hosts) > 1:
            duplicate_ips.append({"ip": ip_addr, "controllers": hosts})

    rack_by_sid = {c["system_id"]: c for c in rack_results}
    dhcp_vlan_issues: list[str] = []
    try:
        fabrics = _normalize_list(client.get("fabrics"))
        for fab in fabrics:
            fid = fab.get("id")
            if fid is None:
                continue
            vlans = _normalize_list(client.get(f"fabrics/{fid}/vlans"))
            for vlan in vlans:
                if not vlan.get("dhcp_on"):
                    continue
                for role in ("primary_rack", "secondary_rack"):
                    rack_sid = vlan.get(role)
                    if not rack_sid:
                        continue
                    rack_ctrl = rack_by_sid.get(rack_sid)
                    if rack_ctrl is None:
                        dhcp_vlan_issues.append(
                            f"VLAN {vlan.get('vid', '?')} (fabric {fid}): "
                            f"{role} {rack_sid} not found in rack controllers"
                        )
                    elif rack_ctrl["status"] == "offline":
                        dhcp_vlan_issues.append(
                            f"VLAN {vlan.get('vid', '?')} (fabric {fid}): "
                            f"{role} {rack_ctrl['hostname']} is offline"
                        )
    except Exception:
        pass

    net_issues: list[str] = []
    for dup in duplicate_ips:
        net_issues.append(f"Duplicate IP {dup['ip']} on: {', '.join(dup['controllers'])}")
    for h in no_ip_controllers:
        net_issues.append(f"{h} has no IP addresses")
    net_issues.extend(dhcp_vlan_issues)
    for issue in net_issues:
        all_issues.append(f"networking: {issue}")

    networking_ok = len(net_issues) == 0

    # --- Aggregated SSH commands ---
    ssh_commands: list[str] = []
    for c in rack_results + region_results:
        if c["issues"] and c["ssh_commands"]:
            first_ip = c["ip_addresses"][0] if c["ip_addresses"] else "?"
            summary_line = "; ".join(c["issues"])
            ssh_commands.append(f"# {c['hostname']} ({first_ip}) -- {summary_line}")
            ssh_commands.append(c["ssh_commands"].get("check_systemd_services", ""))
            ssh_commands.append(c["ssh_commands"].get("check_disk_usage", ""))
            if c["node_type"] in ("region", "region+rack"):
                ssh_commands.append(c["ssh_commands"].get("check_regiond_logs", ""))

    ok = (
        controllers_ok
        and db_result.get("ok", True)
        and proxy_ok
        and images_result.get("ok", True)
        and len(version_issues) == 0
        and networking_ok
    )

    categories_with_issues = sum([
        not controllers_ok,
        not db_result.get("ok", True) and not db_result.get("skipped"),
        not proxy_ok,
        not images_result.get("ok", True) and not images_result.get("skipped"),
        len(version_issues) > 0,
        not networking_ok,
    ])
    summary = (
        f"{len(all_issues)} issue(s) found across {categories_with_issues} categor"
        f"{'y' if categories_with_issues == 1 else 'ies'}"
        if all_issues
        else "All checks passed"
    )

    return {
        "ok": ok,
        "summary": summary,
        "controllers": {
            "ok": controllers_ok,
            "rack_controllers": rack_results,
            "region_controllers": region_results,
        },
        "database": db_result,
        "proxy": {
            "ok": proxy_ok,
            "http_proxy": http_proxy,
            "services": proxy_services,
            "issues": proxy_issues,
        },
        "images": images_result,
        "versions": {
            "ok": len(version_issues) == 0,
            "expected": expected_version,
            "controllers": version_details,
            "issues": version_issues,
        },
        "networking": {
            "ok": networking_ok,
            "dhcp_vlan_issues": dhcp_vlan_issues,
            "duplicate_ips": duplicate_ips,
            "controllers_with_no_ips": no_ip_controllers,
            "issues": net_issues,
        },
        "issues": all_issues,
        "ssh_commands": [cmd for cmd in ssh_commands if cmd],
    }


@app.command("validate-health")
def validate_health(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    skip_db: bool = typer.Option(False, "--skip-db", help="Skip database health check"),
    skip_images: bool = typer.Option(False, "--skip-images", help="Skip boot image check"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Comprehensive MAAS deployment health check.

    Validates 6 categories: controller services (rack + region), database
    connectivity, Squid proxy cache, boot image sync, version consistency,
    and controller IP/DHCP networking.
    """
    name, client = _get_client(site)

    db_url: str | None = None
    if not skip_db:
        if site:
            db_url = os.environ.get(f"MAAS_{site.upper()}_DB_URL") or os.environ.get("MAAS_DB_URL")
        else:
            db_url = os.environ.get("MAAS_DB_URL")

    result = _validate_health(client, db_url=db_url, check_images=not skip_images)

    if json_output:
        _output(result, as_json=True)
        if not result["ok"]:
            raise typer.Exit(1)
        return

    status_icon = "PASS" if result["ok"] else "FAIL"
    typer.echo(f"# MAAS Health Check ({name}): {status_icon}")
    typer.echo(f"  {result['summary']}\n")

    categories = [
        ("Controllers", result["controllers"]["ok"], result["controllers"]),
        ("Database", result["database"].get("ok", True), result["database"]),
        ("Proxy", result["proxy"]["ok"], result["proxy"]),
        ("Images", result["images"].get("ok", True), result["images"]),
        ("Versions", result["versions"]["ok"], result["versions"]),
        ("Networking", result["networking"]["ok"], result["networking"]),
    ]

    for cat_name, cat_ok, cat_data in categories:
        icon = "PASS" if cat_ok else "FAIL"
        skipped = cat_data.get("skipped")
        if skipped:
            typer.echo(f"  [{cat_name}] SKIP — {cat_data.get('reason', 'skipped')}")
        else:
            typer.echo(f"  [{cat_name}] {icon}")
        for issue in cat_data.get("issues", []):
            typer.echo(f"    - {issue}")

    if result["ssh_commands"]:
        typer.echo("\n# SSH follow-up commands:")
        for cmd in result["ssh_commands"]:
            typer.echo(f"  {cmd}")

    if not result["ok"]:
        raise typer.Exit(1)


@app.command("verify-image-cache")
def verify_image_cache(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    osystem: str | None = typer.Option(None, "--os", help="Filter by OS (e.g. 'ubuntu')"),
    series: str | None = typer.Option(None, "--series", help="Filter by series (e.g. 'jammy')"),
    fix: bool = typer.Option(
        False, "--fix", help="Trigger boot resource re-import if issues detected"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Verify boot image cache health before deploying.

    Checks that synced boot resources exist for the target OS/series,
    that no import is in progress, and that rack controller HTTP services
    are healthy.

    Use --fix to trigger a boot resource re-import if problems are found.

    For file-level squashfs verification on the rack controller, use SSH:

        ssh <rack-controller> stat /var/cache/maas/httpproxy/<hash>
        ssh <rack-controller> sha256sum /var/cache/maas/httpproxy/<hash>

    Example:

        maas-cli verify-image-cache --os ubuntu --series jammy
        maas-cli verify-image-cache --os ubuntu --series jammy --fix
    """
    _, client = _get_client(site)
    result = _check_boot_resources(client, osystem=osystem, series=series)

    if fix and result["issues"]:
        typer.echo("  Issues detected, triggering boot resource re-import...", err=True)
        try:
            client.post("boot-resources", params={"op": "import"})
            result["fix_applied"] = True
            result["fix_action"] = "boot-resources import triggered"
        except Exception as e:
            result["fix_applied"] = False
            result["fix_error"] = str(e)

    if json_output:
        _output(result, as_json=True)
    else:
        status = "HEALTHY" if result["ok"] else "UNHEALTHY"
        typer.echo(f"  Image cache: {status}")
        typer.echo(f"  Synced resources: {result['total_synced_resources']}")
        typer.echo(f"  Matched: {len(result['matched_resources'])}")
        typer.echo(f"  Importing: {result['is_importing']}")
        for r in result["matched_resources"]:
            size_mb = round(r["size"] / (1024 * 1024), 1) if r.get("size") else "?"
            typer.echo(f"    {r['name']:<40} arch={r['architecture']}  size={size_mb}MB")
        for rh in result["rack_controllers"]:
            typer.echo(f"    rack {rh['hostname']}: http={rh['http_service']}")
        if result["issues"]:
            typer.echo("  Issues:")
            for issue in result["issues"]:
                typer.echo(f"    ⚠ {issue}")
        if result.get("fix_applied"):
            typer.echo(f"  Fix: {result['fix_action']}")
        elif result.get("fix_error"):
            typer.echo(f"  Fix failed: {result['fix_error']}")


@app.command()
def results(
    system_id: str = typer.Argument(help="Machine system_id"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    result_type: str | None = typer.Option(
        None, "--type", "-t", help="Filter: commissioning, testing, installation"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List commissioning/testing script results for a machine."""
    _, client = _get_client(site)
    params: dict[str, Any] = {}
    if result_type:
        params["type"] = result_type
    try:
        result = _normalize_list(client.get(f"nodes/{system_id}/results", params=params or None))
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            nb_res = _resolve_via_netbox(system_id, client)
            if nb_res.system_id:
                system_id = nb_res.system_id
                result = _normalize_list(
                    client.get(f"nodes/{system_id}/results", params=params or None)
                )
            else:
                _handle_404(
                    e,
                    resource="system_id",
                    identifier=system_id,
                    command="results",
                    netbox=nb_res,
                )
        else:
            raise
    _output(result, as_json=json_output)


@app.command()
def tags(
    tag_name: str | None = typer.Argument(None, help="Tag name (list machines with this tag)"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all tags, or list machines with a specific tag."""
    _, client = _get_client(site)
    if tag_name:
        result = _normalize_list(client.get(f"tags/{tag_name}", params={"op": "machines"}))
    else:
        result = _normalize_list(client.get("tags"))
    _output(result, as_json=json_output)


@app.command(name="subnet-stats")
def subnet_stats(
    subnet_id: int = typer.Argument(help="Subnet ID"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get IP utilization statistics for a subnet."""
    _, client = _get_client(site)
    stats = client.get(f"subnets/{subnet_id}", params={"op": "statistics"})
    _output(stats, as_json=json_output)


@app.command()
def config(
    name: str | None = typer.Argument(None, help="Config key (e.g. default_osystem, maas_name)"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Read MAAS global configuration."""
    _, client = _get_client(site)
    if name:
        value = client.get("maas", params={"op": "get_config", "name": name})
        _output({"name": name, "value": value}, as_json=json_output)
    else:
        keys = [
            "maas_name",
            "default_osystem",
            "default_distro_series",
            "commissioning_distro_series",
            "kernel_opts",
            "ntp_servers",
            "upstream_dns",
            "http_proxy",
            "network_discovery",
        ]
        result = {}
        for k in keys:
            try:
                result[k] = client.get("maas", params={"op": "get_config", "name": k})
            except Exception:
                result[k] = None
        _output(result, as_json=json_output)


_CLI_RESOURCE_TYPES = {
    "discoveries": "discovery",
    "resource_pools": "resourcepools",
    "notifications": "notifications",
    "dhcp_snippets": "dhcp-snippets",
    "region_controllers": "regioncontrollers",
    "scripts": "scripts",
    "users": "users",
}


@app.command()
def resources(
    resource_type: str = typer.Argument(
        help="Resource type: discoveries, resource_pools, notifications, dhcp_snippets, region_controllers, scripts, users"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    script_type: str | None = typer.Option(
        None, "--type", "-t", help="Filter scripts: commissioning, testing"
    ),
    whoami: bool = typer.Option(False, "--whoami", help="For users: show only current user"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List MAAS resources by type."""
    rt = resource_type.strip().lower()
    endpoint = _CLI_RESOURCE_TYPES.get(rt)
    if not endpoint:
        typer.echo(f"Error: resource_type must be one of {list(_CLI_RESOURCE_TYPES)}", err=True)
        raise typer.Exit(1)

    _, client = _get_client(site)
    params: dict[str, Any] = {}
    if rt == "scripts" and script_type:
        params["type"] = script_type
    if rt == "users" and whoami:
        params["op"] = "whoami"
        result = client.get(endpoint, params=params)
        _output(result, as_json=json_output)
        return
    result = _normalize_list(client.get(endpoint, params=params or None))
    _output(result, as_json=json_output)


_OP_EXPECTED: dict[str, dict[str, list[str]]] = {
    "power_on": {"success_power": ["on"]},
    "power_off": {"success_power": ["off"]},
    "commission": {
        "in_progress": ["Commissioning"],
        "success": ["Ready"],
    },
    "deploy": {
        "in_progress": ["Deploying"],
        "success": ["Deployed"],
    },
    "release": {
        "in_progress": ["Releasing", "Disk erasing"],
        "success": ["Ready"],
    },
}


def _redfish_hints() -> str:
    """Return Redfish CLI hints for verifying power state and console."""
    cli = REDFISH_HINTS.as_cli_hints(host="<oob_ip>")
    return (
        "\n  Hints — verify via Redfish/BMC (use oob_ip from NetBox):\n"
        f"    Power state:  {cli['power_state']}\n"
        f"    VGA console:  {cli['screenshot']}\n"
        f"    Watch boot:   {cli['watch_boot']}\n"
    )


_OP_DEFAULT_TIMEOUTS: dict[str, int] = {
    "deploy": 600,
    "release": 600,
    "commission": 900,
    "power_on": 120,
    "power_off": 120,
    "power_cycle": 120,
}


@app.command()
def op(
    system_id: str = typer.Argument(help="Machine system_id"),
    operation: str = typer.Argument(
        help="Operation: power_on, power_off, power_cycle, commission, "
        "deploy, release, mark_broken, mark_fixed, exit_rescue_mode",
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Poll until operation converges (default: wait)"
    ),
    wait_timeout: int | None = typer.Option(
        None,
        "--timeout",
        help=(
            "Max seconds to poll. Defaults per operation: "
            "deploy=600, release=600, commission=900, power=120"
        ),
    ),
    osystem: str | None = typer.Option(
        None,
        "--osystem",
        help="OS to deploy (e.g. 'ubuntu'). Only used with op=deploy.",
    ),
    distro_series: str | None = typer.Option(
        None,
        "--distro-series",
        help="Distro series to deploy (e.g. 'jammy', 'noble'). Only used with op=deploy.",
    ),
    preflight_cache: bool = typer.Option(
        False,
        "--preflight-cache",
        help="Check boot image cache health before deploy (only for op=deploy).",
    ),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Run a lifecycle operation on a machine (WRITE operation).

    Uses fire-then-poll: sends the operation with a short timeout, then
    polls machine status until it converges or the deadline expires.

    For deploy, pass --osystem and --distro-series to select the OS:

        maas-cli op <system_id> deploy --osystem ubuntu --distro-series jammy --yes

    Default timeouts: deploy=600s, release=600s (disk erase can be slow),
    commission=900s, power ops=120s. Override with --timeout.
    """
    import time

    effective_timeout = (
        wait_timeout if wait_timeout is not None else _OP_DEFAULT_TIMEOUTS.get(operation, 120)
    )

    if osystem and operation != "deploy":
        typer.echo("Warning: --osystem is only used with op=deploy, ignoring.", err=True)
    if distro_series and operation != "deploy":
        typer.echo("Warning: --distro-series is only used with op=deploy, ignoring.", err=True)

    if not confirm:
        extra = ""
        if operation == "deploy" and (osystem or distro_series):
            extra = f" (osystem={osystem or 'default'}, distro_series={distro_series or 'default'})"
        typer.confirm(f"Run '{operation}'{extra} on machine {system_id}?", abort=True)

    _, client = _get_client(site)

    # Preflight: verify boot image cache before deploy
    if preflight_cache and operation == "deploy":
        typer.echo("  Preflight: checking boot image cache...")
        cache_result = _check_boot_resources(client, osystem=osystem, series=distro_series)
        if not cache_result["ok"]:
            typer.echo("  ⚠ Image cache preflight FAILED:", err=True)
            for issue in cache_result["issues"]:
                typer.echo(f"    - {issue}", err=True)
            typer.echo(
                "  Aborting deploy. Fix with: maas-cli verify-image-cache --fix",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"  Preflight OK: {len(cache_result['matched_resources'])} image(s) available")
    elif preflight_cache and operation != "deploy":
        typer.echo("Warning: --preflight-cache is only used with op=deploy, ignoring.", err=True)

    # Check current state before firing
    try:
        machine = client.get(f"machines/{system_id}")
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            nb_res = _resolve_via_netbox(system_id, client)
            if nb_res.system_id:
                system_id = nb_res.system_id
                machine = client.get(f"machines/{system_id}")
            else:
                _handle_404(
                    e,
                    resource="system_id",
                    identifier=system_id,
                    command="op",
                    netbox=nb_res,
                )
        else:
            raise
    cur_status = machine.get("status_name", "")
    cur_power = machine.get("power_state", "")
    expected = _OP_EXPECTED.get(operation, {})

    # Already in target state?
    if cur_power in expected.get("success_power", []):
        typer.echo(f"  already {operation}: power={cur_power}")
        return
    if cur_status in expected.get("success", []):
        typer.echo(f"  already {operation}: status={cur_status}")
        return

    # Already in progress? Skip POST, just poll.
    in_progress_states = expected.get("in_progress", [])
    if cur_status in in_progress_states:
        typer.echo(f"  already {cur_status} — skipping duplicate request, polling...")
        timed_out = False
    else:
        # Build POST data for the operation
        post_data: dict[str, Any] = {}
        if operation == "deploy":
            if osystem:
                post_data["osystem"] = osystem
            if distro_series:
                post_data["distro_series"] = distro_series

        # Fire with short timeout (10s) — tolerate timeouts
        result, timed_out = client.post_fire(
            f"machines/{system_id}",
            data=post_data,
            params={"op": operation},
        )

    if timed_out:
        typer.echo(f"  {operation} sent (MAAS accepted but didn't respond in time, polling...)")
    else:
        status = result.get("status_name", "") if isinstance(result, dict) else ""
        power = result.get("power_state", "") if isinstance(result, dict) else ""
        typer.echo(f"  {operation} accepted → status={status}  power={power}")

    if not wait:
        out = {
            "ok": True,
            "system_id": system_id,
            "op": operation,
            "accepted": True,
            "timed_out": timed_out,
            "converged": False,
            "hints": REDFISH_HINTS.as_cli_hints(host="<oob_ip>"),
        }
        if json_output:
            _output(out, as_json=True)
        else:
            typer.echo(_redfish_hints())
        return

    # Poll until converged
    start = time.time()
    poll_interval = 5
    while (time.time() - start) < effective_timeout:
        time.sleep(poll_interval)
        elapsed = int(time.time() - start)
        machine = _get_machine_for_poll(client, system_id, command="op")
        status = machine.get("status_name", "")
        power = machine.get("power_state", "")
        typer.echo(f"  [{elapsed}s] status={status}  power={power}")

        if power in expected.get("success_power", []):
            out = {
                "ok": True,
                "system_id": system_id,
                "op": operation,
                "converged": True,
                "status": status,
                "power_state": power,
                "elapsed_s": elapsed,
            }
            if json_output:
                _output(out, as_json=True)
            else:
                typer.echo(f"  ✓ {operation} complete ({elapsed}s)")
            return
        if status in expected.get("success", []):
            out = {
                "ok": True,
                "system_id": system_id,
                "op": operation,
                "converged": True,
                "status": status,
                "power_state": power,
                "elapsed_s": elapsed,
            }
            if json_output:
                _output(out, as_json=True)
            else:
                typer.echo(f"  ✓ {operation} complete ({elapsed}s)")
            return

        if poll_interval < 10:
            poll_interval += 1

    # Timed out waiting
    elapsed = int(time.time() - start)
    out = {
        "ok": True,
        "system_id": system_id,
        "op": operation,
        "converged": False,
        "status": status,
        "power_state": power,
        "elapsed_s": elapsed,
        "timeout_s": effective_timeout,
        "message": f"Still {status} after {elapsed}s — operation is in progress",
        "hints": REDFISH_HINTS.as_cli_hints(host="<oob_ip>"),
    }
    if json_output:
        _output(out, as_json=True)
    else:
        typer.echo(f"  ⚠ still {status} after {elapsed}s — operation in progress")
        typer.echo(_redfish_hints())


_WAIT_STATES: dict[str, dict[str, list[str]]] = {
    "deployed": {
        "success": ["Deployed"],
        "failure": ["Failed deployment"],
    },
    "ready": {
        "success": ["Ready"],
        "failure": ["Failed commissioning", "Failed testing"],
    },
    "on": {
        "success_power": ["on"],
    },
    "off": {
        "success_power": ["off"],
    },
    "commissioning": {
        "in_progress": ["Commissioning"],
        "success": ["Ready"],
        "failure": ["Failed commissioning"],
    },
}

_WAIT_DEFAULTS: dict[str, int] = {
    "deployed": 600,
    "ready": 600,
    "on": 120,
    "off": 120,
    "commissioning": 900,
}


@app.command()
def wait(
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    hostname: list[str] | None = typer.Option(
        None,
        "--hostname",
        "-H",
        help="Hostname(s) to wait on (repeatable or comma-separated)",
    ),
    system_id: str | None = typer.Option(None, "--id", help="System ID to wait on"),
    until: str = typer.Option(
        ...,
        "--until",
        "-u",
        help="Target state: deployed, ready, on, off, commissioning",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        "-t",
        help="Max seconds to poll (default depends on target state)",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Poll until machine(s) reach a target state.

    Designed for background execution — run with block_until_ms=0 and
    check the terminal file later. The agent can kill the PID to cancel.

    Examples:
        maas-cli wait -s central -H gpu104 --until deployed
        maas-cli wait -s central -H gpu037,gpu081 --until ready -t 300
        maas-cli wait -s central --id m7qs4a --until on
    """
    import time

    target = until.lower()
    if target not in _WAIT_STATES:
        typer.echo(
            f"Error: --until must be one of: {', '.join(sorted(_WAIT_STATES))}",
            err=True,
        )
        raise typer.Exit(1)

    states = _WAIT_STATES[target]
    max_wait = timeout if timeout is not None else _WAIT_DEFAULTS.get(target, 300)

    clients = _build_clients(site)
    client_name = next(iter(clients))
    client = clients[client_name]

    hostnames: list[str] = []
    if hostname:
        for h in hostname:
            hostnames.extend(p.strip() for p in h.split(",") if p.strip())

    if not hostnames and not system_id:
        typer.echo("Error: provide --hostname or --id", err=True)
        raise typer.Exit(1)

    # Resolve targets: list of (system_id, hostname)
    targets: list[tuple[str, str]] = []
    if system_id:
        try:
            m = client.get(f"machines/{system_id}")
        except RuntimeError as e:
            if is_maas_http_error(e, 404):
                nb_res = _resolve_via_netbox(system_id, client)
                if nb_res.system_id:
                    system_id = nb_res.system_id
                    m = client.get(f"machines/{system_id}")
                else:
                    _handle_404(
                        e,
                        resource="system_id",
                        identifier=system_id,
                        command="wait",
                        netbox=nb_res,
                    )
            else:
                raise
        targets.append((system_id, m.get("hostname", system_id)))
    else:
        params: dict[str, Any] = {}
        if len(hostnames) == 1:
            params["hostname"] = hostnames[0]
        else:
            params["hostname"] = hostnames
        machines = _normalize_list(client.get("machines", params=params))
        for m in machines:
            targets.append((m["system_id"], m.get("hostname", m["system_id"])))

    if not targets:
        typer.echo("Error: no machines found matching the filter", err=True)
        typer.echo(
            "Hint: MAAS hostnames (e.g. gpu001) differ from NetBox device names. "
            "Use --id with NetBox-style names only if NETBOX_URL/NETBOX_TOKEN are set "
            "(resolution via Provider_Machine_ID), or pass the MAAS hostname with -H.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Waiting for {len(targets)} machine(s) → {target}  (timeout={max_wait}s)")
    pending = dict(targets)  # sid -> hostname
    results: dict[str, dict[str, Any]] = {}
    start = time.time()
    poll_interval = 5

    while pending and (time.time() - start) < max_wait:
        time.sleep(poll_interval)
        elapsed = int(time.time() - start)

        for sid in list(pending):
            m = _get_machine_for_poll(client, sid, command="wait")
            status = m.get("status_name", "")
            power = m.get("power_state", "")
            name = pending[sid]

            if power in states.get("success_power", []) or status in states.get("success", []):
                typer.echo(f"  ✓ {name} → {target}  ({elapsed}s)")
                results[sid] = {
                    "hostname": name,
                    "ok": True,
                    "status": status,
                    "power": power,
                    "elapsed_s": elapsed,
                }
                del pending[sid]
            elif status in states.get("failure", []):
                typer.echo(f"  ✗ {name} FAILED: {status}  ({elapsed}s)")
                results[sid] = {
                    "hostname": name,
                    "ok": False,
                    "status": status,
                    "power": power,
                    "elapsed_s": elapsed,
                }
                del pending[sid]
            else:
                typer.echo(f"  [{elapsed}s] {name}: status={status}  power={power}")

        if poll_interval < 15:
            poll_interval += 1

    elapsed = int(time.time() - start)
    for sid, name in pending.items():
        m = _get_machine_for_poll(client, sid, command="wait")
        status = m.get("status_name", "")
        power = m.get("power_state", "")
        typer.echo(f"  ⚠ {name}: still {status} after {elapsed}s")
        results[sid] = {
            "hostname": name,
            "ok": False,
            "status": status,
            "power": power,
            "elapsed_s": elapsed,
            "timed_out": True,
        }

    all_ok = all(r.get("ok") for r in results.values())
    if json_output:
        _output({"ok": all_ok, "results": results, "elapsed_s": elapsed}, as_json=True)
    else:
        done = sum(1 for r in results.values() if r.get("ok"))
        typer.echo(f"\n# {done}/{len(results)} reached {target}")
        if pending:
            typer.echo(_redfish_hints())

    raise typer.Exit(0 if all_ok else 1)


@app.command(name="create-bond")
def create_bond(
    system_id: str = typer.Argument(help="Machine system_id"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    name: str = typer.Option("bond0", "--name", "-n", help="Bond interface name"),
    parents_csv: str | None = typer.Option(
        None,
        "--parents",
        "-p",
        help="Comma-separated parent interface names (e.g. enp48s0np0,enp49s0np1). Auto-detected if omitted.",
    ),
    mode: str = typer.Option("802.3ad", "--mode", "-m", help="Bond mode"),
    xmit_hash: str = typer.Option("layer3+4", "--xmit-hash", help="Transmit hash policy"),
    lacp_rate: str = typer.Option("fast", "--lacp-rate", help="LACP rate"),
    primary: str | None = typer.Option(
        None, "--primary", help="Primary interface name for active-backup bonds"
    ),
    mtu: int = typer.Option(9000, "--mtu", help="MTU"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a bond interface on a machine (WRITE operation)."""
    if not confirm:
        typer.confirm(f"Create bond '{name}' on machine {system_id}?", abort=True)

    _, client = _get_client(site)
    try:
        machine = client.get(f"machines/{system_id}")
    except RuntimeError as e:
        if is_maas_http_error(e, 404):
            nb_res = _resolve_via_netbox(system_id, client)
            if nb_res.system_id:
                system_id = nb_res.system_id
                machine = client.get(f"machines/{system_id}")
            else:
                _handle_404(
                    e,
                    resource="system_id",
                    identifier=system_id,
                    command="create-bond",
                    netbox=nb_res,
                )
        else:
            raise
    hostname = machine.get("hostname", "")
    status = machine.get("status_name", "")

    if status not in ("Ready", "Allocated", "Deployed"):
        typer.echo(
            f"Error: machine {system_id} is '{status}'; must be Ready, Allocated, or Deployed",
            err=True,
        )
        raise typer.Exit(1)

    iface_set = machine.get("interface_set") or []
    physical = [i for i in iface_set if i.get("type") == "physical"]

    existing_bonds = [i["name"] for i in iface_set if i.get("type") == "bond"]
    if name in existing_bonds:
        typer.echo(f"Error: bond '{name}' already exists on {system_id}", err=True)
        raise typer.Exit(1)

    if parents_csv:
        parent_names = [p.strip() for p in parents_csv.split(",") if p.strip()]
    else:
        ranked = sorted(physical, key=lambda i: i.get("link_speed", 0) or 0, reverse=True)
        if len(ranked) < 2:
            typer.echo(
                f"Error: only {len(ranked)} physical interface(s); need at least 2", err=True
            )
            raise typer.Exit(1)
        parent_names = [ranked[0]["name"], ranked[1]["name"]]
        typer.echo(f"  auto-detected parents: {parent_names}")

    iface_by_name = {i["name"]: i for i in iface_set}
    parent_ids: list[str] = []
    for pname in parent_names:
        iface = iface_by_name.get(pname)
        if not iface:
            typer.echo(f"Error: interface '{pname}' not found on {system_id}", err=True)
            raise typer.Exit(1)
        parent_ids.append(str(iface["id"]))

    create_data: dict[str, Any] = {
        "name": name,
        "parents": parent_ids,
        "bond_mode": mode.replace("+", "%2B"),
        "bond_xmit_hash_policy": xmit_hash.replace("+", "%2B"),
        "bond_lacp_rate": lacp_rate.replace("+", "%2B"),
        "mtu": str(mtu),
    }
    if primary:
        create_data["bond_primary"] = primary

    result = client.post(
        f"nodes/{system_id}/interfaces",
        data=create_data,
        params={"op": "create_bond"},
    )
    bond_id = result.get("id") if isinstance(result, dict) else None

    out: dict[str, Any] = {
        "ok": True,
        "system_id": system_id,
        "hostname": hostname,
        "bond": {"name": name, "id": bond_id, "parents": parent_names},
    }
    if json_output:
        _output(out, as_json=True)
    else:
        typer.echo(f"  ✓ created bond {name} (id={bond_id}) on {hostname} ({system_id})")
        typer.echo(f"    parents: {', '.join(parent_names)}")
        typer.echo(f"    mode={mode}  xmit_hash={xmit_hash}  lacp_rate={lacp_rate}  mtu={mtu}")


def _run_bond_update(
    *,
    system_id: str,
    site: str | None,
    yes: bool,
    allow_write: bool,
    confirm_msg: str,
    coro_factory,
    json_output: bool,
    result_key: str,
) -> None:
    """Shared runner for update-bond-mode and update-bond-primary CLI commands."""
    if not allow_write:
        typer.echo("Error: pass --allow-write to modify MAAS / database", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(confirm_msg, abort=True)

    from maas_mcp.server import _initialize

    try:
        _initialize(Settings())
    except Exception as e:
        typer.echo(f"Error: failed to initialize MAAS/NetBox: {e}", err=True)
        raise typer.Exit(1) from e

    instance = (site or "default").lower()

    try:
        result = asyncio.run(coro_factory(_PrintContext(), instance))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    structured = _extract_tool_result(result)
    if json_output:
        _output(structured, as_json=True)
    else:
        typer.echo(
            f"  ok={structured.get('ok')} method={structured.get('method')} "
            f"{result_key}={structured.get(f'{result_key}_after')}"
        )
        if structured.get("note"):
            typer.echo(f"  {structured['note']}")


@app.command("update-bond-mode")
def update_bond_mode_cmd(
    system_id: str = typer.Argument(help="Machine system_id"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    bond_name: str = typer.Option("bond0", "--bond", "-b", help="Bond interface name"),
    mode: str = typer.Option(
        "active-backup", "--mode", "-m", help="Target bond_mode (e.g. active-backup)"
    ),
    database_fallback: bool = typer.Option(
        False,
        "--database-fallback/--no-database-fallback",
        help="Patch Postgres if MAAS API ignores the change (Deployed machines)",
    ),
    allow_write: bool = typer.Option(False, "--allow-write", help="Required to perform writes"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Update bond_mode on an existing bond (API; optional DB fallback for Deployed)."""
    from maas_mcp.server import maas_update_bond_mode

    async def _coro(ctx: Any, instance: str) -> Any:
        return await maas_update_bond_mode(
            ctx,
            instance=instance,
            system_id=system_id,
            machine_id=None,
            bond_name=bond_name,
            bond_mode=mode,
            database_fallback=database_fallback,
            allow_write=True,
        )

    _run_bond_update(
        system_id=system_id,
        site=site,
        yes=yes,
        allow_write=allow_write,
        confirm_msg=(
            f"Update bond {bond_name!r} to bond_mode={mode!r} on {system_id!r} "
            f"(database_fallback={database_fallback})?"
        ),
        coro_factory=_coro,
        json_output=json_output,
        result_key="bond_mode",
    )


@app.command("update-bond-primary")
def update_bond_primary_cmd(
    system_id: str = typer.Argument(help="Machine system_id"),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    bond_name: str = typer.Option("bond0", "--bond", "-b", help="Bond interface name"),
    primary: str = typer.Option(..., "--primary", "-p", help="Primary interface name (e.g. enp211s0np0)"),
    database_fallback: bool = typer.Option(
        False,
        "--database-fallback/--no-database-fallback",
        help="Patch Postgres if MAAS API ignores the change (Deployed machines)",
    ),
    allow_write: bool = typer.Option(False, "--allow-write", help="Required to perform writes"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Update bond_primary on an existing bond (API; optional DB fallback for Deployed)."""
    from maas_mcp.server import maas_update_bond_primary

    async def _coro(ctx: Any, instance: str) -> Any:
        return await maas_update_bond_primary(
            ctx,
            instance=instance,
            system_id=system_id,
            machine_id=None,
            bond_name=bond_name,
            bond_primary=primary,
            database_fallback=database_fallback,
            allow_write=True,
        )

    _run_bond_update(
        system_id=system_id,
        site=site,
        yes=yes,
        allow_write=allow_write,
        confirm_msg=(
            f"Update bond {bond_name!r} bond_primary={primary!r} on {system_id!r} "
            f"(database_fallback={database_fallback})?"
        ),
        coro_factory=_coro,
        json_output=json_output,
        result_key="bond_primary",
    )


@app.command("sync-network-config")
def sync_network_config_cmd(
    system_id: str = typer.Argument(help="Target machine system_id (destination MAAS)"),
    site: str | None = typer.Option(
        None,
        "--site",
        "-s",
        help="Target MAAS instance name (uses 'default' / MAAS_DEFAULT_SITE if omitted)",
    ),
    source_site: str = typer.Option(
        "ori",
        "--source-site",
        help="Source MAAS instance to read bond/IP profile from",
    ),
    source_system_id: str | None = typer.Option(
        None,
        "--source-id",
        help="system_id on source MAAS (optional; match by hostname/MAC if omitted)",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview only (default: on)",
    ),
    allow_write: bool = typer.Option(
        False,
        "--allow-write/--no-allow-write",
        help="Allow MAAS API writes (requires --no-dry-run)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt for writes"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print result as JSON"),
):
    """Replicate bond/MTU/IP from source MAAS to target (MCP maas_sync_network_config)."""
    if not dry_run and allow_write and not yes:
        typer.confirm(
            "Write to MAAS: create bonds and assign IPs on the target machine?",
            abort=True,
        )

    from maas_mcp.server import _initialize, maas_sync_network_config

    try:
        _initialize(Settings())
    except Exception as e:
        typer.echo(f"Error: failed to initialize MAAS/NetBox: {e}", err=True)
        raise typer.Exit(1) from e

    instance = (site or "default").lower()
    src = source_site.lower()

    async def _run() -> Any:
        ctx = _PrintContext()
        return await maas_sync_network_config(
            ctx,
            instance=instance,
            source_instance=src,
            system_id=system_id,
            machine_id=None,
            source_system_id=source_system_id,
            dry_run=dry_run,
            allow_write=allow_write,
        )

    try:
        result = asyncio.run(_run())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    _output(_extract_tool_result(result), as_json=json_output)


@app.command("migrate-node")
def migrate_node_cmd(
    system_id: str = typer.Argument(help="Target machine system_id (destination MAAS)"),
    site: str | None = typer.Option(
        None,
        "--site",
        "-s",
        help="Target MAAS instance name (uses 'default' / MAAS_DEFAULT_SITE if omitted)",
    ),
    source_site: str = typer.Option(
        "ori",
        "--source-site",
        help="Legacy/source MAAS instance",
    ),
    source_system_id: str | None = typer.Option(
        None,
        "--source-id",
        help="system_id on source MAAS (optional; match by hostname/MAC if omitted)",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview all steps (default: on)",
    ),
    allow_write: bool = typer.Option(
        False,
        "--allow-write/--no-allow-write",
        help="Apply changes (requires --no-dry-run for network writes)",
    ),
    sync_interfaces: bool = typer.Option(
        True,
        "--sync-interfaces/--no-sync-interfaces",
        help="Replicate physical interfaces (MACs + names) from source",
    ),
    sync_power: bool = typer.Option(
        True,
        "--sync-power/--no-sync-power",
        help="Copy power parameters from source",
    ),
    sync_network: bool = typer.Option(
        True,
        "--sync-network/--no-sync-network",
        help="Create bonds and assign static IPs",
    ),
    sync_metadata: bool = typer.Option(
        True,
        "--sync-metadata/--no-sync-metadata",
        help="Copy hostname, zone, pool, arch, cpu, memory, OS from source",
    ),
    sync_disks: bool = typer.Option(
        True,
        "--sync-disks/--no-sync-disks",
        help="Copy block device records from source",
    ),
    sync_tags: bool = typer.Option(
        True,
        "--sync-tags/--no-sync-tags",
        help="Create tags from source and associate machine",
    ),
    sync_hardware_info: bool = typer.Option(
        True,
        "--sync-hardware-info/--no-sync-hardware-info",
        help="Copy hardware_info via direct DB (requires MAAS_*_DB_URL)",
    ),
    sync_numa_devices: bool = typer.Option(
        True,
        "--sync-numa-devices/--no-sync-numa-devices",
        help="Sync NUMA topology, PCI/USB devices, and interface speeds via DB",
    ),
    set_deployed: bool = typer.Option(
        False,
        "--set-deployed/--no-set-deployed",
        help="Mark machine Deployed (status=6) with admin owner after sync",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation for writes"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print result as JSON"),
):
    """Full per-node migration: interfaces + power + network + disks + tags + metadata + hardware_info + NUMA."""
    if not dry_run and allow_write and not yes:
        typer.confirm(
            "Apply migration (all enabled steps write to target MAAS)?",
            abort=True,
        )

    from maas_mcp.server import _initialize, maas_migrate_node

    try:
        _initialize(Settings())
    except Exception as e:
        typer.echo(f"Error: failed to initialize MAAS/NetBox: {e}", err=True)
        raise typer.Exit(1) from e

    instance = (site or "default").lower()
    src = source_site.lower()

    async def _run() -> Any:
        ctx = _PrintContext()
        return await maas_migrate_node(
            ctx,
            instance=instance,
            source_instance=src,
            system_id=system_id,
            machine_id=None,
            source_system_id=source_system_id,
            dry_run=dry_run,
            allow_write=allow_write,
            sync_interfaces=sync_interfaces,
            sync_power=sync_power,
            sync_network=sync_network,
            sync_disks=sync_disks,
            sync_tags=sync_tags,
            sync_metadata=sync_metadata,
            sync_hardware_info=sync_hardware_info,
            sync_numa_devices=sync_numa_devices,
            set_deployed=set_deployed,
        )

    try:
        result = asyncio.run(_run())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    _output(_extract_tool_result(result), as_json=json_output)


@app.command()
def resolve(
    identifier: str = typer.Argument(
        help="NetBox device name or MAAS hostname/system_id to resolve"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Resolve a NetBox device name to a MAAS system_id.

    Uses custom_fields.Provider_Machine_ID to bridge NetBox device names
    (e.g. 'research-common-h100-001') to MAAS hostnames (e.g. 'gpu001')
    and then to MAAS system_id.

    Requires NETBOX_URL and NETBOX_TOKEN to be set.

    Examples:

        maas-cli resolve research-common-h100-001
        maas-cli resolve gpu001
    """
    _, client = _get_client(site)
    nb_result = _resolve_via_netbox(identifier, client)

    result: dict[str, Any] = {
        "identifier": identifier,
        "resolved": nb_result.ok,
        "system_id": nb_result.system_id,
        "maas_hostname": nb_result.maas_hostname,
    }
    if nb_result.failure:
        result["failure"] = nb_result.failure.value
        hint = format_netbox_resolution_hint(nb_result)
        if hint:
            result["hint"] = hint
    if nb_result.detail:
        result["detail"] = nb_result.detail

    if nb_result.ok and nb_result.system_id:
        try:
            machine = client.get(f"machines/{nb_result.system_id}")
            result["machine"] = {
                "system_id": machine.get("system_id"),
                "hostname": machine.get("hostname"),
                "status_name": machine.get("status_name"),
                "power_state": machine.get("power_state"),
            }
        except Exception:
            pass

    if json_output:
        _output(result, as_json=True)
    else:
        if nb_result.ok:
            typer.echo(
                f"  ✓ {identifier} → system_id={nb_result.system_id} "
                f"(hostname={nb_result.maas_hostname})"
            )
            if result.get("machine"):
                m = result["machine"]
                typer.echo(f"    status={m['status_name']}  power={m['power_state']}")
        else:
            typer.echo(f"  ✗ Could not resolve {identifier!r}", err=True)
            if nb_result.failure:
                typer.echo(f"    reason: {nb_result.failure.value}", err=True)
            hint = format_netbox_resolution_hint(nb_result)
            if hint:
                typer.echo(f"    {hint}", err=True)
            raise typer.Exit(1)


@app.command(name="bond-audit")
def bond_audit(
    hostname: str | None = typer.Option(
        None, "--hostname", "-H", help="MAAS hostname(s), comma-separated"
    ),
    cluster: str | None = typer.Option(
        None, "--cluster", "-c", help="NetBox cluster name to resolve all active nodes"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="MAAS site/instance"),
    mismatches_only: bool = typer.Option(
        False, "--mismatches-only", "-m", help="Only show mismatches"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    bond_name: str = typer.Option("bond0", "--bond-name", help="Bond interface name"),
    max_workers: int = typer.Option(20, "--max-workers", help="Max parallel SSH connections"),
):
    """Compare live bond active slave (SSH) vs MAAS bond configuration.

    Detects when the active NIC on a node's bond0 differs from what MAAS
    records as the primary parent. Useful for verifying bond failover state
    and catching persistent misconfigurations.

    Requires at least one of --hostname or --cluster.

    Examples:

        maas-cli bond-audit --hostname ori-gpu001
        maas-cli bond-audit --cluster research-common-h100
        maas-cli bond-audit --hostname ori-gpu001,ori-gpu005 --mismatches-only
        maas-cli bond-audit --cluster research-common-h100 --json
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not hostname and not cluster:
        typer.echo("Error: provide at least one of --hostname or --cluster", err=True)
        raise typer.Exit(1)

    _, client = _get_client(site)

    hostnames_list: list[str] = []
    ssh_targets: dict[str, str] = {}

    if cluster:
        try:
            st = Settings()
        except Exception:
            st = None
        if not st or not st.netbox_url or not st.netbox_token:
            typer.echo("Error: NETBOX_URL and NETBOX_TOKEN required for --cluster", err=True)
            raise typer.Exit(1)
        from maas_mcp.netbox_client import NetboxClient

        nb = NetboxClient(url=str(st.netbox_url), token=st.netbox_token.get_secret_value())
        resolved = resolve_cluster_hostnames(nb, cluster)
        if not resolved:
            typer.echo(f"Error: no active devices found in cluster {cluster!r}", err=True)
            raise typer.Exit(1)
        for entry in resolved:
            hostnames_list.append(entry["maas_hostname"])
            ssh_targets[entry["maas_hostname"]] = entry["maas_hostname"]
        if not json_output:
            typer.echo(f"Resolved {len(resolved)} nodes from cluster {cluster!r}", err=True)

    if hostname:
        for h in hostname.split(","):
            h = h.strip()
            if h and h not in hostnames_list:
                hostnames_list.append(h)
                ssh_targets[h] = h

    maas_data = resolve_maas_hostnames(client, hostnames_list)

    ssh_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(ssh_bond_info, ssh_targets[h], bond_name): h for h in hostnames_list
        }
        for future in as_completed(futures):
            h = futures[future]
            try:
                ssh_results[h] = future.result()
            except Exception as exc:
                ssh_results[h] = {"active_slave": None, "slaves": [], "error": str(exc)}

    results: list[dict] = []
    for h in hostnames_list:
        maas_info = maas_data.get(h, {})
        system_id = maas_info.get("system_id")
        machine = maas_info.get("machine")
        maas_error = maas_info.get("error")

        if maas_error:
            results.append(build_audit_result(h, system_id, ssh_results.get(h, {}), None))
            results[-1]["error"] = maas_error
            continue

        maas_bond = extract_maas_bond_config(machine, bond_name) if machine else None
        results.append(build_audit_result(h, system_id, ssh_results.get(h, {}), maas_bond))

    if mismatches_only:
        results = [r for r in results if r.get("match") is False or r.get("error")]

    summary = build_summary(results)

    if json_output:
        _output({"results": results, "summary": summary}, as_json=True)
    else:
        for r in results:
            status = "ERROR" if r.get("error") else ("OK" if r.get("match") else "MISMATCH")
            marker = {"OK": "  [ok]", "MISMATCH": "  [!!]", "ERROR": "  [err]"}.get(status, "  [??]")
            line = f"{marker} {r['hostname']}"
            if r.get("error"):
                line += f"  error={r['error']}"
            else:
                line += (
                    f"  ssh_active={r['ssh_active_slave']}"
                    f"  maas_primary={r['maas_effective_primary']}"
                )
                if not r.get("match"):
                    line += "  << MISMATCH"
            typer.echo(line)

        typer.echo("")
        typer.echo(
            f"Summary: {summary['total']} total, "
            f"{summary['matches']} match, "
            f"{summary['mismatches']} mismatch, "
            f"{summary['errors']} errors"
        )


@app.command(name="create-token")
def create_token(
    url: str = typer.Option(..., "--url", "-u", help="MAAS URL (e.g. http://maas.example.com:5240/MAAS)"),
    username: str = typer.Option(..., "--username", help="MAAS username"),
    password: str = typer.Option(..., "--password", help="MAAS password"),
    token_name: str = typer.Option("agent-token", "--name", help="Token name/description"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Generate a MAAS API key from username/password credentials.

    Authenticates via the MAAS session login API and creates an
    authorisation token. The resulting consumer_key:token_key:token_secret
    is the API key for MAAS_API_KEY.

    Example:
        maas-cli create-token --url http://maas:5240/MAAS --username admin --password secret
    """
    import requests

    session = requests.Session()

    login_url = f"{url.rstrip('/')}/accounts/login/"
    try:
        session.get(login_url, verify=False)
        csrf_token = session.cookies.get("csrftoken", "")
    except Exception as exc:
        typer.echo(f"Error: could not reach {login_url}: {exc}", err=True)
        raise typer.Exit(1) from None

    try:
        login_resp = session.post(
            login_url,
            data={"username": username, "password": password, "csrfmiddlewaretoken": csrf_token},
            headers={"Referer": login_url, "X-CSRFToken": csrf_token},
            verify=False,
            allow_redirects=False,
        )
        if login_resp.status_code not in (200, 302):
            typer.echo(f"Error: login failed (HTTP {login_resp.status_code})", err=True)
            raise typer.Exit(1)
    except requests.RequestException as exc:
        typer.echo(f"Error: login request failed: {exc}", err=True)
        raise typer.Exit(1) from None

    api_url = f"{url.rstrip('/')}/api/2.0/account/"
    csrf_token = session.cookies.get("csrftoken", csrf_token)
    try:
        token_resp = session.post(
            api_url,
            params={"op": "create_authorisation_token"},
            data={"name": token_name},
            headers={"X-CSRFToken": csrf_token, "Referer": api_url},
            verify=False,
        )
        if token_resp.status_code not in (200, 201):
            typer.echo(f"Error: token creation failed (HTTP {token_resp.status_code}): {token_resp.text[:200]}", err=True)
            raise typer.Exit(1)
    except requests.RequestException as exc:
        typer.echo(f"Error: token creation request failed: {exc}", err=True)
        raise typer.Exit(1) from None

    token_data = token_resp.json()
    consumer_key = token_data.get("consumer_key", "")
    token_key = token_data.get("token_key", "")
    token_secret = token_data.get("token_secret", "")
    api_key = f"{consumer_key}:{token_key}:{token_secret}"

    if json_output:
        typer.echo(json.dumps({
            "api_key": api_key,
            "consumer_key": consumer_key,
            "token_key": token_key,
            "token_secret": token_secret,
            "name": token_name,
            "url": url,
        }, indent=2))
    else:
        typer.echo(f"MAAS API Key: {api_key}")
        typer.echo(f"  consumer_key: {consumer_key}")
        typer.echo(f"  token_key: {token_key}")
        typer.echo(f"  token_secret: {token_secret}")
        typer.echo(f"\nSet in environment: export MAAS_<SITE>_API_KEY=\"{api_key}\"")

    session.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
