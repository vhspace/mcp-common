"""
netbox-cli: Thin CLI wrapper around the NetBox REST API.

Provides the same capabilities as netbox-mcp but via shell commands,
enabling AI agents to use NetBox with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import difflib
import ipaddress
import json
import os
import re

import click
import typer
from mcp_common.agent_remediation import install_cli_exception_handler
from mcp_common.logging import setup_logging
from typer.core import TyperGroup

from netbox_mcp.netbox_client import NetBoxRestClient
from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES

logger = setup_logging(
    name="netbox-cli",
    level=os.environ.get("NETBOX_CLI_LOG_LEVEL", "WARNING"),
    json_output=os.environ.get("NETBOX_CLI_LOG_JSON", "").lower() in ("1", "true", "yes"),
    system_log=True,
)


class _NetBoxGroup(TyperGroup):
    """Typer group that suggests the closest valid command on typos."""

    def resolve_command(self, ctx: click.Context, args: list[str]) -> tuple:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            cmd_name = args[0] if args else None
            if not cmd_name:
                raise

            available = sorted(self.list_commands(ctx))
            matches = difflib.get_close_matches(cmd_name, available, n=3, cutoff=0.4)
            wants_json = "--json" in args or "-j" in args

            if wants_json:
                error_data = {
                    "error": f"Unknown command '{cmd_name}'",
                    "suggestions": matches,
                    "available_commands": available,
                }
                click.echo(json.dumps(error_data, indent=2), err=True)
                raise SystemExit(2) from None

            if matches:
                suggestions = ", ".join(f"'{m}'" for m in matches)
                click.echo(f"\nDid you mean: {suggestions}?", err=True)
            else:
                click.echo(
                    f"\nAvailable commands: {', '.join(available)}",
                    err=True,
                )

            raise click.UsageError(f"No such command '{cmd_name}'.") from None


app = typer.Typer(
    name="netbox-cli",
    help="Query NetBox infrastructure data. Use --help on any subcommand for details.",
    no_args_is_help=True,
    cls=_NetBoxGroup,
)
install_cli_exception_handler(app, project_repo="vhspace/netbox-mcp", logger=logger)


def _client() -> NetBoxRestClient:
    url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        typer.echo("Error: NETBOX_URL and NETBOX_TOKEN env vars required", err=True)
        raise typer.Exit(1)
    verify = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
    return NetBoxRestClient(url=url, token=token, verify_ssl=verify)


def _resolve_cluster_id(client: NetBoxRestClient, name: str) -> int | None:
    """Resolve a cluster name to its ID via exact-match lookup.

    The NetBox ``cluster`` filter on devices does icontains matching,
    so we must resolve to ``cluster_id`` for precise results.
    """
    resp = client.get("virtualization/clusters", params={"name": name, "limit": 1})
    results = resp.get("results", []) if isinstance(resp, dict) else resp
    if results and results[0].get("name") == name:
        return results[0]["id"]
    return None


def _resolve_site_id(client: NetBoxRestClient, name: str) -> int | None:
    """Resolve a site name to its ID via exact-match lookup.

    The NetBox ``site`` query parameter expects a slug, so we resolve
    to ``site_id`` (integer) which works regardless of slug format.
    """
    resp = client.get("dcim/sites", params={"name": name, "limit": 1})
    results = resp.get("results", []) if isinstance(resp, dict) else resp
    if results and results[0].get("name") == name:
        return results[0]["id"]
    return None


_FILTER_SPLIT_RE = re.compile(r",\s*(?=\w+\s*=)")


def _parse_filter_string(raw: str) -> dict[str, str]:
    """Parse a filter string into key-value pairs.

    Splits on commas only when followed by a filter key (``word=``),
    so commas *within* values are preserved.  NetBox uses in-value
    commas for OR semantics on a single field (e.g. ``status=active,planned``).

    Examples::

        "site=ori-tx,cluster=cartesia5"
            → {"site": "ori-tx", "cluster": "cartesia5"}     (AND)
        "status=active,planned"
            → {"status": "active,planned"}                    (OR within field)
        "site=ori-tx,status=active,planned,cluster=cartesia5"
            → {"site": "ori-tx", "status": "active,planned",
               "cluster": "cartesia5"}
    """
    result: dict[str, str] = {}
    for pair in _FILTER_SPLIT_RE.split(raw):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _apply_filters(params: dict, filters: list[str] | None) -> None:
    """Merge one or more ``--filter`` values into *params* in-place."""
    if not filters:
        return
    for raw in filters:
        params.update(_parse_filter_string(raw))


def _resolve_name_filters(client: NetBoxRestClient, params: dict) -> None:
    """Resolve name-based filter values to IDs for precise matching.

    NetBox text filters like ``cluster=name`` do icontains matching which
    can return incorrect results.  This resolves known filters to their
    ``_id`` equivalents.
    """
    if "cluster" in params and "cluster_id" not in params:
        cluster_name = params.pop("cluster")
        resolved = _resolve_cluster_id(client, cluster_name)
        if resolved is not None:
            params["cluster_id"] = str(resolved)
        else:
            params["cluster"] = cluster_name

    if "site" in params and "site_id" not in params:
        site_name = params.pop("site")
        resolved = _resolve_site_id(client, site_name)
        if resolved is not None:
            params["site_id"] = str(resolved)
        else:
            params["site"] = site_name


def _parse_fields(fields: str | None) -> list[str] | None:
    if not fields:
        return None
    return [f.strip() for f in fields.split(",") if f.strip()]


def _is_ip_address(s: str) -> bool:
    """Return True if *s* is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _pick_fields(data: dict | list, fields: list[str] | None) -> dict | list:
    """Filter dict to only requested fields."""
    if not fields:
        return data
    if isinstance(data, list):
        return [{k: v for k, v in item.items() if k in fields} for item in data]
    return {k: v for k, v in data.items() if k in fields}


def _format_device_line(d: dict) -> str:
    """One-line compact summary of a device."""
    name = d.get("name", "?")
    status = d.get("status", {})
    status_val = status.get("value", status) if isinstance(status, dict) else status
    site = d.get("site", {})
    site_name = site.get("name", site) if isinstance(site, dict) else site
    role = d.get("role", d.get("device_role", {}))
    role_name = role.get("name", role) if isinstance(role, dict) else role
    dtype = d.get("device_type", {})
    model = dtype.get("model", dtype) if isinstance(dtype, dict) else dtype

    primary = d.get("primary_ip4_address") or d.get("primary_ip4", {})
    if isinstance(primary, dict):
        primary = primary.get("address", "")
    oob = d.get("oob_ip_address") or d.get("oob_ip", {})
    if isinstance(oob, dict):
        oob = oob.get("address", "")

    cluster = d.get("cluster", {})
    cluster_name = cluster.get("name", cluster) if isinstance(cluster, dict) else cluster

    parts = [name]
    if status_val:
        parts.append(f"status={status_val}")
    if site_name:
        parts.append(f"site={site_name}")
    if cluster_name:
        parts.append(f"cluster={cluster_name}")
    if role_name:
        parts.append(f"role={role_name}")
    if model:
        parts.append(f"model={model}")
    if primary:
        parts.append(f"primary_ip={primary}")
    if oob:
        parts.append(f"oob_ip={oob}")
    provider_id = d.get("provider_machine_id") or d.get("custom_fields", {}).get("Provider_Machine_ID")
    if provider_id:
        parts.append(f"provider_id={provider_id}")
    return "  ".join(parts)


def _output(
    data: object, as_json: bool = False, compact: bool = True, show_limit_hint: bool = True
) -> None:
    """Print output — compact text by default, JSON with --json."""
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, dict) and "results" in data:
        count = data.get("count", len(data["results"]))
        results = data["results"]
        shown = len(results)
        if shown < count:
            header = f"# {count} result(s) (showing {shown})"
            if show_limit_hint:
                header += f" — use --limit {count} to see all"
            typer.echo(header)
        else:
            typer.echo(f"# {count} result(s)")
        for item in results:
            if compact and "name" in item and ("site" in item or "device_type" in item):
                typer.echo(_format_device_line(item))
            else:
                for k, v in item.items():
                    if isinstance(v, dict):
                        v = v.get("name", v.get("display", v.get("address", v)))
                    typer.echo(f"  {k}: {v}")
                typer.echo()
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                v = v.get("name", v.get("display", v.get("address", v)))
            elif isinstance(v, list) and len(v) > 5:
                v = f"[{len(v)} items]"
            typer.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            typer.echo(item)
    else:
        typer.echo(data)


@app.command()
def lookup(
    hostname: str = typer.Argument(
        help="Hostname, partial name, provider machine ID, or IP address to look up"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="Filter results by site name"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    fields: str | None = typer.Option(
        None, "--fields", "-f", help="Comma-separated fields to return"
    ),
):
    """Resolve a device by hostname, provider machine ID, or IP address.

    Searches by NetBox device name first, then falls back to the
    Provider_Machine_ID custom field, then tries IP address lookup
    if the input looks like an IP. For broader exploration, use 'search'.
    """
    client = _client()
    field_list = _parse_fields(fields)
    site_id: str | None = None
    if site:
        resolved = _resolve_site_id(client, site)
        if resolved is None:
            typer.echo(f"Error: site '{site}' not found in NetBox.", err=True)
            raise typer.Exit(1)
        site_id = str(resolved)

    params: dict = {"name__ic": hostname, "limit": 5}
    if site_id:
        params["site_id"] = site_id
    if field_list:
        params["fields"] = ",".join(field_list)
    result = client.get("dcim/devices", params=params)

    if isinstance(result, dict) and not result.get("results"):
        fallback_params: dict = {"cf_Provider_Machine_ID": hostname, "limit": 5}
        if site_id:
            fallback_params["site_id"] = site_id
        if field_list:
            fallback_params["fields"] = ",".join(field_list)
        result = client.get("dcim/devices", params=fallback_params)
        if isinstance(result, dict) and result.get("count", 0) > 50:
            result = {"count": 0, "results": []}

    if isinstance(result, dict) and not result.get("results") and _is_ip_address(hostname):
        ip_resp = client.get("ipam/ip-addresses", params={"address": hostname, "limit": 5})
        ip_results = ip_resp.get("results", []) if isinstance(ip_resp, dict) else []
        device_ids_seen: set[int] = set()
        ip_devices: list[dict] = []
        for ip_obj in ip_results:
            assigned = ip_obj.get("assigned_object") or {}
            dev_ref = assigned.get("device") or {}
            dev_id = dev_ref.get("id")
            if dev_id and dev_id not in device_ids_seen:
                device_ids_seen.add(dev_id)
                device = client.get("dcim/devices", id=dev_id)
                if isinstance(device, dict) and device.get("id"):
                    ip_devices.append(device)
        if ip_devices:
            result = {"count": len(ip_devices), "results": ip_devices}

    if isinstance(result, dict) and result.get("results"):
        for device in result["results"]:
            pip4 = device.get("primary_ip4")
            if isinstance(pip4, dict) and "address" in pip4:
                device["primary_ip4_address"] = pip4["address"].split("/")[0]
            oob = device.get("oob_ip")
            if isinstance(oob, dict) and "address" in oob:
                device["oob_ip_address"] = oob["address"].split("/")[0]

    _output(
        result if isinstance(result, dict) else {"results": result, "count": len(result)},
        as_json=json_output,
        show_limit_hint=False,
    )


_DEFAULT_SEARCH_TYPES = [
    "dcim.device",
    "dcim.site",
    "ipam.ipaddress",
    "dcim.interface",
    "dcim.rack",
    "ipam.vlan",
    "virtualization.cluster",
]


@app.command()
def search(
    query: str = typer.Argument(help="Search term (hostname, IP, serial, etc.)"),
    types: str | None = typer.Option(
        None,
        "--types",
        "-t",
        help="Comma-separated object types (e.g. dcim.device,ipam.ip_address)",
    ),
    status: str | None = typer.Option(
        None, "--status", help="Filter devices by status when search matches a cluster"
    ),
    limit: int = typer.Option(5, "--limit", "-l", help="Max results per type"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Search across multiple NetBox object types by keyword.

    Auto-expands cluster matches to show member devices with site info.
    For exact device hostname resolution, use 'lookup'.
    """
    client = _client()
    search_types = [t.strip() for t in types.split(",")] if types else list(_DEFAULT_SEARCH_TYPES)
    all_results: dict[str, list] = {}
    for otype in search_types:
        type_info = NETBOX_OBJECT_TYPES.get(otype)
        if not type_info:
            typer.echo(f"# Unknown type: {otype}", err=True)
            continue
        endpoint = type_info["endpoint"]
        try:
            resp = client.get(endpoint, params={"q": query, "limit": limit})
            results = resp.get("results", []) if isinstance(resp, dict) else resp
            if results:
                all_results[otype] = results
        except Exception:
            pass

    _cluster_expand_threshold = 20
    cluster_devices: dict[str, dict] = {}
    cluster_results = all_results.get("virtualization.cluster", [])
    if cluster_results:
        for cluster in cluster_results:
            cname = cluster.get("name", "")
            cid = cluster.get("id")
            if not cname or not cid:
                continue
            dev_params: dict = {"cluster_id": cid, "limit": _cluster_expand_threshold}
            if status:
                dev_params["status"] = status
            try:
                dev_resp = client.get("dcim/devices", params=dev_params)
                if isinstance(dev_resp, dict):
                    dev_results = dev_resp.get("results", [])
                    dev_count = dev_resp.get("count", len(dev_results))
                    sites = sorted(
                        {
                            d.get("site", {}).get("name", "?")
                            for d in dev_results
                            if isinstance(d.get("site"), dict)
                        }
                    )
                    cluster_devices[cname] = {
                        "cluster_id": cluster.get("id"),
                        "count": dev_count,
                        "sites": sites,
                        "results": dev_results,
                    }
            except Exception:
                pass

    if json_output:
        output: dict = dict(all_results)
        if cluster_devices:
            output["cluster_devices"] = cluster_devices
        _output(output, as_json=True)
    else:
        if not all_results and not cluster_devices:
            typer.echo("No results found.")
            return

        for otype, items in all_results.items():
            if otype == "virtualization.cluster":
                continue
            typer.echo(f"\n## {otype} ({len(items)})")
            for item in items:
                name = item.get("name") or item.get("display") or item.get("address", "?")
                item_id = item.get("id", "?")
                typer.echo(f"  [{item_id}] {name}")

        for cname, cdata in cluster_devices.items():
            cluster_id = cdata["cluster_id"]
            dev_count = cdata["count"]
            sites = cdata["sites"]
            devices = cdata["results"]
            status_label = f"status={status}" if status else "all statuses"

            typer.echo(f"\n## Cluster: {cname} (id={cluster_id})")
            device_word = "device" if dev_count == 1 else "devices"
            typer.echo(f"  {dev_count} {device_word} ({status_label})")
            if sites:
                typer.echo(f"  Sites: {', '.join(sites)}")

            hint_cmd = f"netbox-cli devices --cluster {cname} --limit 100"
            if status:
                hint_cmd += f" --status {status}"

            if dev_count <= _cluster_expand_threshold:
                if devices:
                    typer.echo()
                    for dev in devices:
                        typer.echo(f"  {_format_device_line(dev)}")
            else:
                typer.echo(f"  Use: {hint_cmd}")

        for cluster in cluster_results:
            cname = cluster.get("name", "")
            if cname not in cluster_devices:
                typer.echo("\n## virtualization.cluster")
                typer.echo(f"  [{cluster.get('id', '?')}] {cname}")


@app.command()
def get(
    object_type: str = typer.Argument(help="Object type (e.g. dcim.device, ipam.ip_address)"),
    object_id: int = typer.Argument(help="Object ID"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get a single object by type and ID."""
    type_info = NETBOX_OBJECT_TYPES.get(object_type)
    if not type_info:
        typer.echo(
            f"Error: Unknown object type '{object_type}'. Run 'netbox-cli types' for list.",
            err=True,
        )
        raise typer.Exit(1)
    client = _client()
    result = client.get(type_info["endpoint"], id=object_id)
    field_list = _parse_fields(fields)
    if field_list and isinstance(result, dict):
        result = _pick_fields(result, field_list)
    _output(result, as_json=json_output)


@app.command(name="list")
def list_objects(
    object_type: str = typer.Argument(help="Object type (e.g. dcim.device, dcim.site)"),
    filters: list[str] | None = typer.Option(
        None,
        "--filter",
        help="Filters as key=value (repeatable). e.g. --filter site=ori-tx --filter cluster=cartesia5",
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    limit: int = typer.Option(100, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    brief: bool = typer.Option(False, "--brief", "-b", help="Brief output (fewer fields)"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List objects of a given type with optional filters."""
    type_info = NETBOX_OBJECT_TYPES.get(object_type)
    if not type_info:
        typer.echo(
            f"Error: Unknown object type '{object_type}'. Run 'netbox-cli types' for list.",
            err=True,
        )
        raise typer.Exit(1)
    client = _client()
    params: dict = {"limit": limit, "offset": offset}
    if brief:
        params["brief"] = 1
    _apply_filters(params, filters)
    _resolve_name_filters(client, params)
    field_list = _parse_fields(fields)
    if field_list:
        params["fields"] = ",".join(field_list)
    result = client.get(type_info["endpoint"], params=params)
    _output(result, as_json=json_output)


@app.command()
def changelogs(
    limit: int = typer.Option(100, "--limit", "-l"),
    filters: list[str] | None = typer.Option(
        None,
        "--filter",
        help="Filters as key=value (repeatable). e.g. --filter user=admin --filter action=update",
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Show recent change history."""
    client = _client()
    params: dict = {"limit": limit}
    _apply_filters(params, filters)
    result = client.get("core/object-changes", params=params)
    _output(result, as_json=json_output)


@app.command()
def types(
    query: str | None = typer.Argument(None, help="Filter types by name"),
):
    """List all supported NetBox object types."""
    for key in sorted(NETBOX_OBJECT_TYPES):
        if query and query.lower() not in key.lower():
            continue
        info = NETBOX_OBJECT_TYPES[key]
        typer.echo(f"  {key:<45} → {info['endpoint']}")


def _list_helper(
    object_type: str,
    extra_filters: dict[str, str | None],
    filters: list[str] | None,
    fields: str | None,
    limit: int,
    offset: int,
    brief: bool,
    json_output: bool,
) -> None:
    """Shared logic for alias commands that wrap ``list``."""
    type_info = NETBOX_OBJECT_TYPES.get(object_type)
    if not type_info:
        typer.echo(f"Error: Unknown object type '{object_type}'.", err=True)
        raise typer.Exit(1)

    client = _client()
    params: dict = {"limit": limit, "offset": offset}
    if brief:
        params["brief"] = 1
    for k, v in extra_filters.items():
        if v is not None:
            params[k] = v
    _apply_filters(params, filters)
    _resolve_name_filters(client, params)
    field_list = _parse_fields(fields)
    if field_list:
        params["fields"] = ",".join(field_list)
    result = client.get(type_info["endpoint"], params=params)
    _output(result, as_json=json_output)


# ── Convenience aliases ──────────────────────────────────────────────


@app.command()
def devices(
    cluster: str | None = typer.Option(None, "--cluster", "-c", help="Filter by cluster name"),
    site: str | None = typer.Option(None, "--site", "-s", help="Filter by site name"),
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (active, planned, staged, etc.)"
    ),
    role: str | None = typer.Option(None, "--role", "-r", help="Filter by device role"),
    filters: list[str] | None = typer.Option(
        None, "--filter", help="Extra filters as key=value (repeatable)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    limit: int = typer.Option(200, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    brief: bool = typer.Option(False, "--brief", "-b"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List devices (shortcut for 'list dcim.device')."""
    client = _client()
    cluster_id = None
    if cluster:
        resolved = _resolve_cluster_id(client, cluster)
        if resolved is None:
            typer.echo(f"Error: cluster '{cluster}' not found in NetBox.", err=True)
            raise typer.Exit(1)
        cluster_id = str(resolved)
    site_id = None
    if site:
        resolved = _resolve_site_id(client, site)
        if resolved is None:
            typer.echo(f"Error: site '{site}' not found in NetBox.", err=True)
            raise typer.Exit(1)
        site_id = str(resolved)
    _list_helper(
        object_type="dcim.device",
        extra_filters={
            "cluster_id": cluster_id,
            "site_id": site_id,
            "status": status,
            "role": role,
        },
        filters=filters,
        fields=fields,
        limit=limit,
        offset=offset,
        brief=brief,
        json_output=json_output,
    )


VALID_DEVICE_STATUSES = frozenset(
    {"active", "planned", "staged", "failed", "inventory", "decommissioning", "offline"}
)


def _resolve_device(client: NetBoxRestClient, hostname_or_id: str) -> dict:
    """Resolve a hostname or numeric ID to a device dict.

    Returns the first matching device or exits with an error.
    """
    if hostname_or_id.isdigit():
        try:
            return client.get("dcim/devices", id=int(hostname_or_id))
        except Exception:
            typer.echo(f"Error: device with ID {hostname_or_id} not found.", err=True)
            raise typer.Exit(1)

    resp = client.get("dcim/devices", params={"name__ic": hostname_or_id, "limit": 5})
    results = resp.get("results", []) if isinstance(resp, dict) else resp
    if not results:
        typer.echo(f"Error: no device found matching '{hostname_or_id}'.", err=True)
        raise typer.Exit(1)
    if len(results) > 1:
        names = ", ".join(d.get("name", "?") for d in results)
        typer.echo(
            f"Error: multiple devices match '{hostname_or_id}': {names}. "
            "Use an exact name or numeric ID.",
            err=True,
        )
        raise typer.Exit(1)
    return results[0]


@app.command(name="update-device")
def update_device(
    device: str = typer.Argument(help="Hostname or device ID to update"),
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="New status (active, planned, staged, failed, inventory, decommissioning, offline)",
    ),
    cluster: str | None = typer.Option(
        None, "--cluster", "-c", help="New cluster assignment (by name)"
    ),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required flag to confirm write operation"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Update a device's status or cluster assignment.

    This is a WRITE operation — requires --confirm and VPN connectivity.
    Resolves hostname to device, validates the new values, then PATCHes.
    """
    if not confirm:
        typer.echo(
            "Error: write operations require --confirm flag. "
            "Example: netbox-cli update-device HOST --status active --confirm",
            err=True,
        )
        raise typer.Exit(1)

    if status is None and cluster is None:
        typer.echo("Error: at least one of --status or --cluster must be provided.", err=True)
        raise typer.Exit(1)

    if status is not None and status not in VALID_DEVICE_STATUSES:
        typer.echo(
            f"Error: invalid status '{status}'. Valid: {', '.join(sorted(VALID_DEVICE_STATUSES))}",
            err=True,
        )
        raise typer.Exit(1)

    client = _client()
    device_obj = _resolve_device(client, device)
    device_id = device_obj["id"]
    device_name = device_obj.get("name", device)

    patch_data: dict = {}
    changes: list[str] = []

    if status is not None:
        old_status = device_obj.get("status", {})
        old_val = (
            old_status.get("value", old_status) if isinstance(old_status, dict) else old_status
        )
        patch_data["status"] = status
        changes.append(f"status: {old_val} → {status}")

    if cluster is not None:
        cluster_id = _resolve_cluster_id(client, cluster)
        if cluster_id is None:
            typer.echo(f"Error: cluster '{cluster}' not found in NetBox.", err=True)
            raise typer.Exit(1)
        old_cluster = device_obj.get("cluster", {})
        old_name = (
            old_cluster.get("name", old_cluster) if isinstance(old_cluster, dict) else old_cluster
        )
        patch_data["cluster"] = cluster_id
        changes.append(f"cluster: {old_name} → {cluster}")

    updated = client.patch("dcim/devices", id=device_id, data=patch_data)

    if json_output:
        _output(updated, as_json=True)
    else:
        typer.echo(f"Updated device '{device_name}' (id={device_id}):")
        for change in changes:
            typer.echo(f"  {change}")


@app.command()
def sites(
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    region: str | None = typer.Option(None, "--region", "-r", help="Filter by region"),
    filters: list[str] | None = typer.Option(
        None, "--filter", help="Extra filters as key=value (repeatable)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    limit: int = typer.Option(200, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    brief: bool = typer.Option(False, "--brief", "-b"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List sites (shortcut for 'list dcim.site')."""
    _list_helper(
        object_type="dcim.site",
        extra_filters={"status": status, "region": region},
        filters=filters,
        fields=fields,
        limit=limit,
        offset=offset,
        brief=brief,
        json_output=json_output,
    )


@app.command()
def clusters(
    site: str | None = typer.Option(None, "--site", "-s", help="Filter by site name"),
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    cluster_type: str | None = typer.Option(None, "--type", "-t", help="Filter by cluster type"),
    filters: list[str] | None = typer.Option(
        None, "--filter", help="Extra filters as key=value (repeatable)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    limit: int = typer.Option(200, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    brief: bool = typer.Option(False, "--brief", "-b"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List clusters (shortcut for 'list virtualization.cluster')."""
    site_id = None
    if site:
        resolved = _resolve_site_id(_client(), site)
        if resolved is None:
            typer.echo(f"Error: site '{site}' not found in NetBox.", err=True)
            raise typer.Exit(1)
        site_id = str(resolved)
    _list_helper(
        object_type="virtualization.cluster",
        extra_filters={"site_id": site_id, "status": status, "type": cluster_type},
        filters=filters,
        fields=fields,
        limit=limit,
        offset=offset,
        brief=brief,
        json_output=json_output,
    )


@app.command()
def ips(
    device: str | None = typer.Option(None, "--device", "-d", help="Filter by device name"),
    interface: str | None = typer.Option(
        None, "--interface", "-i", help="Filter by interface name"
    ),
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    filters: list[str] | None = typer.Option(
        None, "--filter", help="Extra filters as key=value (repeatable)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    limit: int = typer.Option(200, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    brief: bool = typer.Option(False, "--brief", "-b"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List IP addresses (shortcut for 'list ipam.ipaddress')."""
    _list_helper(
        object_type="ipam.ipaddress",
        extra_filters={"device": device, "interface": interface, "status": status},
        filters=filters,
        fields=fields,
        limit=limit,
        offset=offset,
        brief=brief,
        json_output=json_output,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
