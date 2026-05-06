"""
weka-cli: Thin CLI wrapper around the Weka REST API.

Provides the same capabilities as weka-mcp but via shell commands,
enabling AI agents to use Weka with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from mcp_common.agent_remediation import install_cli_exception_handler

from weka_mcp.config import Settings
from weka_mcp.site_manager import SiteManager
from weka_mcp.weka_client import WekaRestClient

app = typer.Typer(
    name="weka-cli",
    help="Query and manage Weka distributed storage. Use --help on any subcommand for details.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/weka-mcp")

s3_app = typer.Typer(help="S3 bucket and cluster operations.")
app.add_typer(s3_app, name="s3")

_site_mgr: SiteManager | None = None


def _get_site_mgr() -> SiteManager:
    global _site_mgr
    if _site_mgr is None:
        _site_mgr = SiteManager()
        try:
            settings = Settings()
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from e
        _site_mgr.configure(settings)
    return _site_mgr


def _client(site: str | None = None) -> WekaRestClient:
    mgr = _get_site_mgr()
    return mgr.get_client(site)


def _pick_fields(data: Any, fields: list[str] | None) -> Any:
    if not fields:
        return data
    if isinstance(data, list):
        return [
            {k: v for k, v in item.items() if k in fields}
            for item in data
            if isinstance(item, dict)
        ]
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in fields}
    return data


def _unwrap(resp: Any) -> Any:
    """Weka wraps most responses in {"data": [...]} or {"data": {...}}. Unwrap to the inner value."""
    if isinstance(resp, dict) and "data" in resp and isinstance(resp["data"], (list, dict)):
        return resp["data"]
    return resp


def _format_kv(d: dict, indent: str = "  ") -> str:
    lines = []
    for k, v in d.items():
        if isinstance(v, dict):
            v = v.get("name", v.get("display", json.dumps(v, default=str)))
        elif isinstance(v, list) and len(v) > 5:
            v = f"[{len(v)} items]"
        lines.append(f"{indent}{k}: {v}")
    return "\n".join(lines)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Count items grouped by a dict key."""
    counts: dict[str, int] = {}
    for item in items:
        if isinstance(item, dict):
            val = str(item.get(key, "unknown"))
            counts[val] = counts.get(val, 0) + 1
    return counts


def _summarize_list(items: list[dict[str, Any]], *group_keys: str) -> dict[str, Any]:
    """Build a summary dict with counts for each group key."""
    result: dict[str, Any] = {}
    for key in group_keys:
        result[f"by_{key}"] = _count_by(items, key)
    return result


def _output(data: Any, as_json: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, list):
        typer.echo(f"# {len(data)} result(s)")
        for item in data:
            if isinstance(item, dict):
                name = item.get("name", item.get("hostname", item.get("uid", "?")))
                uid = item.get("uid", item.get("id", ""))
                status = item.get("status", item.get("state", ""))
                parts = [f"[{uid}]" if uid else "", str(name)]
                if status:
                    parts.append(f"status={status}")
                typer.echo("  ".join(p for p in parts if p))
            else:
                typer.echo(f"  {item}")
    elif isinstance(data, dict):
        typer.echo(_format_kv(data))
    else:
        typer.echo(data)


SiteOption = typer.Option(None, "--site", "-s", help="Target Weka site (omit for default)")

# ── Commands ──────────────────────────────────────────────────────


@app.command(name="sites")
def list_sites(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all configured Weka sites."""
    mgr = _get_site_mgr()
    site_list = mgr.list_sites()
    if json_output:
        _output(site_list, as_json=True)
        return
    typer.echo(f"# {len(site_list)} site(s)")
    for s in site_list:
        marker = " *" if s.get("active") else ""
        typer.echo(f"  {s['site']}{marker}  {s['weka_host']}  user={s.get('username', '?')}")


@app.command()
def health(
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Cluster health overview: status, active alerts, license info."""
    c = _client(site)
    cluster = c.get("cluster")
    try:
        alerts = c.get("alerts", params={"severity": "MAJOR,CRITICAL"})
    except Exception:
        alerts = {"error": "could not fetch alerts"}
    try:
        license_info = c.get("license")
    except Exception:
        license_info = {"error": "could not fetch license"}

    result = {"cluster": cluster, "alerts": alerts, "license": license_info}
    if json_output:
        _output(result, as_json=True)
        return

    typer.echo("## Cluster")
    cluster_data = _unwrap(cluster)
    if isinstance(cluster_data, list) and cluster_data:
        cluster_data = cluster_data[0]
    if isinstance(cluster_data, dict):
        for key in ("name", "guid", "release", "status", "hot_spare", "capacity"):
            if key in cluster_data:
                typer.echo(f"  {key}: {cluster_data[key]}")
    else:
        typer.echo(f"  {cluster_data}")

    alert_items = _unwrap(alerts)
    if isinstance(alert_items, list):
        typer.echo(f"\n## Active Alerts ({len(alert_items)})")
        for a in alert_items:
            if isinstance(a, dict):
                typer.echo(
                    f"  [{a.get('type', '?')}] {a.get('title', a.get('description', '?'))}  severity={a.get('severity', '?')}"
                )
            else:
                typer.echo(f"  {a}")
    else:
        typer.echo(f"\n## Alerts\n  {alerts}")

    typer.echo("\n## License")
    lic = _unwrap(license_info)
    if isinstance(lic, list) and lic:
        lic = lic[0]
    if isinstance(lic, dict):
        for key in ("mode", "expiry", "capacity", "usage"):
            if key in lic:
                typer.echo(f"  {key}: {lic[key]}")
    else:
        typer.echo(f"  {license_info}")


@app.command()
def filesystems(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all filesystems with capacity and usage."""
    c = _client(site)
    resp = _unwrap(c.get("fileSystems"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    if not json_output and isinstance(resp, list):
        typer.echo(f"# {len(resp)} filesystem(s)")
        for fs in resp:
            if not isinstance(fs, dict):
                typer.echo(f"  {fs}")
                continue
            name = fs.get("name", "?")
            uid = fs.get("uid", "")
            status = fs.get("status", "")
            total = fs.get("total_capacity") or fs.get("capacity", "")
            used = fs.get("used_total") or fs.get("used", "")
            parts = [f"[{uid}]" if uid else "", name]
            if status:
                parts.append(f"status={status}")
            if total:
                parts.append(f"total={total}")
            if used:
                parts.append(f"used={used}")
            typer.echo("  ".join(p for p in parts if p))
        return
    _output(resp, as_json=json_output)


@app.command()
def containers(
    site: str | None = SiteOption,
    summary: bool = typer.Option(
        False, "--summary", help="Show counts by status/mode instead of full list"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List storage containers (nodes/hosts)."""
    c = _client(site)
    resp = _unwrap(c.get("containers"))
    if summary and isinstance(resp, list):
        result = _summarize_list(resp, "status", "mode")
        result["total_containers"] = len(resp)
        _output(result, as_json=json_output)
        return
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def nodes(
    site: str | None = SiteOption,
    summary: bool = typer.Option(
        False, "--summary", help="Show counts by status instead of full list"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List servers / physical nodes."""
    c = _client(site)
    resp = _unwrap(c.get("servers"))
    if summary and isinstance(resp, list):
        result = _summarize_list(resp, "status")
        result["total_nodes"] = len(resp)
        _output(result, as_json=json_output)
        return
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def drives(
    site: str | None = SiteOption,
    summary: bool = typer.Option(
        False, "--summary", help="Show counts by status/host instead of full list"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all drives (SSD/NVMe)."""
    c = _client(site)
    resp = _unwrap(c.get("drives"))
    if summary and isinstance(resp, list):
        result = _summarize_list(resp, "status", "hostname")
        result["total_drives"] = len(resp)
        _output(result, as_json=json_output)
        return
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def alerts(
    site: str | None = SiteOption,
    severity: str | None = typer.Option(
        None, "--severity", help="Filter: CRITICAL, MAJOR, MINOR, WARNING, INFO"
    ),
    summary: bool = typer.Option(
        False, "--summary", help="Show counts by type/severity instead of full list"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List active cluster alerts."""
    c = _client(site)
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    resp = _unwrap(c.get("alerts", params=params or None))
    if summary and isinstance(resp, list):
        muted = sum(1 for a in resp if isinstance(a, dict) and a.get("is_muted"))
        result: dict[str, Any] = {
            "total_alerts": len(resp),
            "by_type": _count_by(resp, "type"),
            "by_severity": _count_by(resp, "severity"),
            "muted": muted,
        }
        _output(result, as_json=json_output)
        return
    if not json_output and isinstance(resp, list):
        typer.echo(f"# {len(resp)} alert(s)")
        for a in resp:
            if isinstance(a, dict):
                typer.echo(
                    f"  [{a.get('type', '?')}] {a.get('title', a.get('description', '?'))}  severity={a.get('severity', '?')}"
                )
            else:
                typer.echo(f"  {a}")
        return
    _output(resp, as_json=json_output)


@app.command()
def events(
    site: str | None = SiteOption,
    severity: str | None = typer.Option(
        None, "--severity", help="INFO, WARNING, MINOR, MAJOR, CRITICAL"
    ),
    category: str | None = typer.Option(None, "--category", "-c", help="Event category"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max events to return"),
    start_time: str | None = typer.Option(None, "--start", help="Start time (ISO 8601)"),
    end_time: str | None = typer.Option(None, "--end", help="End time (ISO 8601)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Query cluster events for troubleshooting."""
    c = _client(site)
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    if category:
        params["category"] = category
    if limit:
        params["num_results"] = limit
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    resp = _unwrap(c.get("events", params=params or None))
    if not json_output and isinstance(resp, list):
        typer.echo(f"# {len(resp)} event(s)")
        for ev in resp:
            if isinstance(ev, dict):
                ts = ev.get("timestamp", ev.get("time", ""))
                sev = ev.get("severity", "")
                desc = ev.get("description", ev.get("message", "?"))
                typer.echo(f"  {ts}  [{sev}]  {desc}")
            else:
                typer.echo(f"  {ev}")
        return
    _output(resp, as_json=json_output)


@app.command()
def stats(
    site: str | None = SiteOption,
    realtime: bool = typer.Option(False, "--realtime", "-r", help="Live real-time stats"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Cluster performance statistics (IOPS, throughput, latency)."""
    c = _client(site)
    endpoint = "stats/realtime" if realtime else "stats"
    resp = _unwrap(c.get(endpoint))
    _output(resp, as_json=json_output)


@app.command()
def snapshots(
    site: str | None = SiteOption,
    filesystem_uid: str | None = typer.Option(None, "--fs", help="Filter by filesystem UID"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List snapshots, optionally filtered by filesystem."""
    c = _client(site)
    params: dict[str, Any] = {}
    if filesystem_uid:
        params["filesystem_uid"] = filesystem_uid
    resp = _unwrap(c.get("snapshots", params=params or None))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def processes(
    site: str | None = SiteOption,
    summary: bool = typer.Option(
        False, "--summary", help="Show counts by status/type instead of full list"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List running Weka processes."""
    c = _client(site)
    resp = _unwrap(c.get("processes"))
    if summary and isinstance(resp, list):
        result = _summarize_list(resp, "status", "type")
        result["total_processes"] = len(resp)
        _output(result, as_json=json_output)
        return
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command(name="list")
def list_resource(
    resource: str = typer.Argument(
        help="Resource type (alerts, containers, drives, filesystems, servers, snapshots, etc.)"
    ),
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List any Weka resource by type name.

    Resource types: alerts, alert_types, alert_descriptions, containers,
    drives, events, failure_domains, filesystem_groups, filesystems,
    interface_groups, organizations, processes, s3_buckets, servers,
    smb_shares, snapshot_policies, snapshots, tasks, users.
    """
    endpoints: dict[str, str] = {
        "alerts": "alerts",
        "alert_types": "alerts/types",
        "alert_descriptions": "alerts/description",
        "containers": "containers",
        "drives": "drives",
        "events": "events",
        "failure_domains": "failureDomains",
        "filesystem_groups": "fileSystemGroups",
        "filesystems": "fileSystems",
        "interface_groups": "interfaceGroups",
        "organizations": "organizations",
        "processes": "processes",
        "s3_buckets": "s3/buckets",
        "servers": "servers",
        "smb_shares": "smb/shares",
        "snapshot_policies": "snapshotPolicy",
        "snapshots": "snapshots",
        "tasks": "tasks",
        "users": "users",
    }
    endpoint = endpoints.get(resource)
    if not endpoint:
        typer.echo(
            f"Error: Unknown resource '{resource}'. Valid: {', '.join(sorted(endpoints))}", err=True
        )
        raise typer.Exit(1)
    c = _client(site)
    resp = _unwrap(c.get(endpoint))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command(name="get")
def get_resource(
    resource: str = typer.Argument(
        help="Resource type (containers, drives, filesystems, servers, etc.)"
    ),
    uid: str = typer.Argument(help="Resource UID"),
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get a single resource by type and UID.

    Resource types: containers, drives, failure_domains, filesystem_groups,
    filesystems, organizations, processes, servers, snapshot_policies,
    snapshots, users.
    """
    endpoints: dict[str, str] = {
        "containers": "containers",
        "drives": "drives",
        "failure_domains": "failureDomains",
        "filesystem_groups": "fileSystemGroups",
        "filesystems": "fileSystems",
        "organizations": "organizations",
        "processes": "processes",
        "servers": "servers",
        "snapshot_policies": "snapshotPolicy",
        "snapshots": "snapshots",
        "users": "users",
    }
    endpoint = endpoints.get(resource)
    if not endpoint:
        typer.echo(
            f"Error: Unknown resource '{resource}'. Valid: {', '.join(sorted(endpoints))}", err=True
        )
        raise typer.Exit(1)
    c = _client(site)
    resp = c.get(f"{endpoint}/{uid}")
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(_unwrap(resp), field_list)
    _output(resp, as_json=json_output)


@app.command()
def orgs(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List organizations with quotas and capacity allocation."""
    c = _client(site)
    resp = _unwrap(c.get("organizations"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    if not json_output and isinstance(resp, list):
        typer.echo(f"# {len(resp)} organization(s)")
        for org in resp:
            if not isinstance(org, dict):
                typer.echo(f"  {org}")
                continue
            name = org.get("name", "?")
            uid = org.get("uid", "")
            alloc = org.get("allocated_capacity") or org.get("ssd_quota", "")
            used = org.get("used_capacity") or org.get("used_ssd", "")
            parts = [f"[{uid}]" if uid else "", name]
            if alloc:
                parts.append(f"allocated={alloc}")
            if used:
                parts.append(f"used={used}")
            typer.echo("  ".join(p for p in parts if p))
        return
    _output(resp, as_json=json_output)


@app.command()
def users(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List user accounts (useful for debugging auth issues)."""
    c = _client(site)
    resp = _unwrap(c.get("users"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    if not json_output and isinstance(resp, list):
        typer.echo(f"# {len(resp)} user(s)")
        for u in resp:
            if not isinstance(u, dict):
                typer.echo(f"  {u}")
                continue
            username = u.get("username", "?")
            uid = u.get("uid", "")
            role = u.get("role", "")
            org = u.get("org", u.get("organization", ""))
            parts = [f"[{uid}]" if uid else "", username]
            if role:
                parts.append(f"role={role}")
            if org:
                parts.append(f"org={org}")
            typer.echo("  ".join(p for p in parts if p))
        return
    _output(resp, as_json=json_output)


@app.command(name="get-filesystem")
def get_filesystem(
    uid: str = typer.Argument(..., help="Filesystem UID"),
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get details for a single filesystem by UID."""
    c = _client(site)
    resp = c.get(f"fileSystems/{uid}")
    resp = _unwrap(resp)
    if isinstance(resp, list) and resp:
        resp = resp[0]
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command(name="get-org")
def get_org(
    uid: str = typer.Argument(..., help="Organization UID"),
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get details for a single organization by UID."""
    c = _client(site)
    resp = c.get(f"organizations/{uid}")
    resp = _unwrap(resp)
    if isinstance(resp, list) and resp:
        resp = resp[0]
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command(name="cluster-status")
def cluster_status(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get cluster status (release, host counts, IO state). Lighter than health."""
    c = _client(site)
    resp = c.get("cluster")
    resp = _unwrap(resp)
    if isinstance(resp, list) and resp:
        resp = resp[0]
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def quotas(
    filesystem_uid: str = typer.Argument(..., help="Filesystem UID"),
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List directory quotas for a filesystem."""
    c = _client(site)
    resp = _unwrap(c.get(f"fileSystems/{filesystem_uid}/quota"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command(name="create-snapshot")
def create_snapshot(
    filesystem_uid: str = typer.Argument(..., help="Filesystem UID to snapshot"),
    name: str = typer.Argument(..., help="Snapshot name"),
    site: str | None = SiteOption,
    writable: bool = typer.Option(False, "--writable", "-w", help="Make snapshot writable"),
    access_point: str | None = typer.Option(None, "--access-point", help="Mount path for snapshot"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a point-in-time snapshot of a filesystem."""
    c = _client(site)
    payload: dict[str, Any] = {
        "filesystem_uid": filesystem_uid,
        "name": name,
        "is_writable": writable,
    }
    if access_point:
        payload["access_point"] = access_point
    result = c.post("snapshots", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command(name="upload-snapshot")
def upload_snapshot(
    uid: str = typer.Argument(..., help="Snapshot UID to upload"),
    locator: str = typer.Argument(..., help="Object-store locator/bucket"),
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Upload a snapshot to object storage (Snap-to-Object)."""
    c = _client(site)
    result = c.post(f"snapshots/{uid}/upload", json={"locator": locator})
    _output(_unwrap(result), as_json=json_output)


@app.command(name="restore-fs")
def restore_fs(
    source_bucket: str = typer.Argument(..., help="Object-store bucket with snapshot"),
    snapshot_name: str = typer.Argument(..., help="Snapshot name to restore"),
    new_fs_name: str = typer.Argument(..., help="Name for the new filesystem"),
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Restore a filesystem from a snapshot in an object-store bucket."""
    c = _client(site)
    payload = {
        "source_bucket": source_bucket,
        "snapshot_name": snapshot_name,
        "new_fs_name": new_fs_name,
    }
    result = c.post("fileSystems/download", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command(name="manage-alert")
def manage_alert(
    action: str = typer.Argument(..., help="'mute' or 'unmute'"),
    alert_type: str = typer.Argument(..., help="Alert type identifier (e.g. NodeDown)"),
    site: str | None = SiteOption,
    duration_secs: int | None = typer.Option(
        None, "--duration", "-d", help="Mute duration in seconds (required for mute)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Mute or unmute a cluster alert type."""
    c = _client(site)
    if action == "mute":
        if duration_secs is None:
            typer.echo("Error: --duration is required when action is 'mute'", err=True)
            raise typer.Exit(1)
        result = c.put(f"alerts/{alert_type}/mute", json={"expiry": duration_secs})
    elif action == "unmute":
        result = c.put(f"alerts/{alert_type}/unmute")
    else:
        typer.echo(f"Error: invalid action '{action}'. Use 'mute' or 'unmute'.", err=True)
        raise typer.Exit(1)
    _output(_unwrap(result), as_json=json_output)


@app.command(name="fs-groups")
def fs_groups(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List filesystem groups."""
    c = _client(site)
    resp = _unwrap(c.get("fileSystemGroups"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@app.command()
def capacity(
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Cluster-wide capacity summary with per-filesystem breakdown."""
    c = _client(site)
    cluster_raw = c.get("cluster")

    cluster = cluster_raw
    if isinstance(cluster, dict) and "data" in cluster:
        inner = cluster["data"]
        if isinstance(inner, list) and inner:
            cluster = inner[0]
        elif isinstance(inner, dict):
            cluster = inner

    cap: dict[str, Any] = {}
    if isinstance(cluster, dict):
        raw_cap = cluster.get("capacity", {})
        if isinstance(raw_cap, dict):
            cap = dict(raw_cap)
        licensing = cluster.get("licensing", {})
        if isinstance(licensing, dict):
            limits = licensing.get("limits", {})
            usage = licensing.get("usage", {})
            if isinstance(limits, dict) and limits.get("usable_capacity_gb") is not None:
                cap["licensed_usable_gb"] = limits["usable_capacity_gb"]
            if isinstance(usage, dict) and usage.get("drive_capacity_gb") is not None:
                cap["drive_capacity_gb"] = usage["drive_capacity_gb"]

    filesystems_resp = _unwrap(c.get("fileSystems"))

    fs_summary: list[dict[str, Any]] = []
    if isinstance(filesystems_resp, list):
        for fs in filesystems_resp:
            if isinstance(fs, dict):
                fs_summary.append(
                    {
                        "name": fs.get("name"),
                        "uid": fs.get("uid"),
                        "status": fs.get("status"),
                        "total_budget": fs.get("total_budget"),
                        "used_total": fs.get("used_total"),
                        "available_total": fs.get("available_total"),
                        "group_name": fs.get("group_name"),
                    }
                )

    result = {
        "cluster_capacity": cap,
        "filesystem_count": len(fs_summary),
        "filesystems": fs_summary,
    }

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo("## Cluster Capacity")
    if isinstance(cap, dict) and cap:
        for k, v in cap.items():
            typer.echo(f"  {k}: {v}")
    else:
        typer.echo("  (no capacity data)")

    typer.echo(f"\n## Filesystems ({len(fs_summary)})")
    for fs in fs_summary:
        parts = [f"[{fs['uid']}]" if fs.get("uid") else "", fs.get("name", "?")]
        if fs.get("status"):
            parts.append(f"status={fs['status']}")
        if fs.get("total_budget"):
            parts.append(f"total={fs['total_budget']}")
        if fs.get("used_total"):
            parts.append(f"used={fs['used_total']}")
        typer.echo("  ".join(p for p in parts if p))


@app.command(name="delete-fs")
def delete_fs(
    uid: str = typer.Argument(..., help="Filesystem UID to delete"),
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Delete a filesystem by UID. WARNING: destroys all data permanently."""
    c = _client(site)
    result = c.delete(f"fileSystems/{uid}")
    _output(_unwrap(result), as_json=json_output)


@app.command(name="delete-org")
def delete_org(
    uid: str = typer.Argument(..., help="Organization UID to delete"),
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Delete an organization by UID. Removes all users and access."""
    c = _client(site)
    result = c.delete(f"organizations/{uid}")
    _output(_unwrap(result), as_json=json_output)


# ── S3 subcommands ────────────────────────────────────────────────


@s3_app.command()
def buckets(
    site: str | None = SiteOption,
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List S3 buckets."""
    c = _client(site)
    resp = _unwrap(c.get("s3/buckets"))
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    resp = _pick_fields(resp, field_list)
    _output(resp, as_json=json_output)


@s3_app.command()
def status(
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show S3 cluster status."""
    c = _client(site)
    resp = c.get("s3")
    _output(_unwrap(resp), as_json=json_output)


# ── Write commands ────────────────────────────────────────────────


@app.command()
def create_org(
    name: str = typer.Argument(..., help="Organization name"),
    site: str | None = SiteOption,
    ssd_quota_gb: int = typer.Option(..., "--ssd-quota", help="SSD quota in GB"),
    total_quota_gb: int = typer.Option(..., "--total-quota", help="Total quota in GB"),
    username: str = typer.Option(
        None, "--username", help="Initial org admin username (defaults to org name)"
    ),
    password: str = typer.Option(
        None, "--password", help="Initial org admin password (auto-generated if omitted)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a new Weka organization with an initial admin user."""
    import secrets

    payload: dict[str, Any] = {
        "name": name,
        "username": username or name,
        "password": password or secrets.token_urlsafe(24),
        "ssd_quota": ssd_quota_gb * (1024**3),
        "total_quota": total_quota_gb * (1024**3),
    }
    c = _client(site)
    result = c.post("organizations", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command()
def create_user(
    username: str = typer.Argument(..., help="Username"),
    site: str | None = SiteOption,
    password: str = typer.Option(..., "--password", help="Password"),
    role: str = typer.Option("OrgAdmin", "--role", help="User role"),
    org_uid: str | None = typer.Option(
        None,
        "--org-uid",
        help="[Ignored] Users are created in the org of the authenticated session. Set WEKA_ORG to target a specific org.",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a new Weka user in the org of the authenticated session (set WEKA_ORG to target a specific org)."""
    if org_uid:
        typer.echo(
            "Note: --org-uid is ignored. Users are created in the org of the authenticated session. "
            "Set WEKA_ORG to target a specific org.",
            err=True,
        )
    c = _client(site)
    payload: dict[str, Any] = {"username": username, "password": password, "role": role}
    result = c.post("users", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command()
def create_fs_group(
    name: str = typer.Argument(..., help="Filesystem group name"),
    site: str | None = SiteOption,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a new filesystem group."""
    c = _client(site)
    result = c.post("fileSystemGroups", json={"name": name})
    _output(_unwrap(result), as_json=json_output)


@app.command()
def create_fs(
    name: str = typer.Argument(..., help="Filesystem name"),
    capacity: str = typer.Argument(..., help="Capacity (e.g. 20TB, 300GB)"),
    site: str | None = SiteOption,
    group_name: str | None = typer.Option(None, "--group", help="Filesystem group name"),
    auth_required: bool = typer.Option(
        True, "--auth-required/--no-auth", help="Require auth for mount"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Create a new filesystem."""
    c = _client(site)
    _units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
    cap_upper = capacity.upper().strip()
    total_bytes = None
    for suffix, mult in sorted(_units.items(), key=lambda x: -len(x[0])):
        if cap_upper.endswith(suffix):
            total_bytes = int(float(cap_upper[: -len(suffix)]) * mult)
            break
    if total_bytes is None:
        total_bytes = int(capacity)
    payload: dict[str, Any] = {
        "name": name,
        "total_capacity": total_bytes,
        "auth_required": auth_required,
    }
    if group_name:
        payload["group_name"] = group_name
    result = c.post("fileSystems", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command()
def update_org_quota(
    org_uid: str = typer.Argument(..., help="Organization UID"),
    site: str | None = SiteOption,
    ssd_quota: str | None = typer.Option(None, "--ssd-quota", help="SSD quota (e.g. 520TB, 1PB)"),
    total_quota: str | None = typer.Option(
        None, "--total-quota", help="Total quota (e.g. 520TB, 1PB)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Update SSD and/or total capacity quotas for an organization."""
    if ssd_quota is None and total_quota is None:
        typer.echo("Error: at least one of --ssd-quota or --total-quota must be provided", err=True)
        raise typer.Exit(1)
    _units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}

    def _to_bytes(val: str) -> int:
        v = val.upper().strip()
        for suffix, mult in sorted(_units.items(), key=lambda x: -len(x[0])):
            if v.endswith(suffix):
                return int(float(v[: -len(suffix)]) * mult)
        return int(val)

    payload: dict[str, Any] = {}
    if ssd_quota is not None:
        payload["ssd_quota"] = _to_bytes(ssd_quota)
    if total_quota is not None:
        payload["total_quota"] = _to_bytes(total_quota)
    c = _client(site)
    result = c.put(f"organizations/{org_uid}/limits", json=payload)
    _output(_unwrap(result), as_json=json_output)


@app.command()
def update_fs(
    uid: str = typer.Argument(..., help="Filesystem UID"),
    site: str | None = SiteOption,
    total_capacity: str | None = typer.Option(
        None, "--total-capacity", help="New total capacity (e.g. 500TB)"
    ),
    ssd_capacity: str | None = typer.Option(
        None, "--ssd-capacity", help="New SSD capacity (tiered only)"
    ),
    new_name: str | None = typer.Option(None, "--new-name", help="Rename the filesystem"),
    auth_required: bool | None = typer.Option(
        None, "--auth-required/--no-auth-required", help="Require auth for mount"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Update a filesystem's capacity, name, or auth settings."""
    payload: dict[str, Any] = {}
    if total_capacity is not None:
        payload["total_capacity"] = total_capacity
    if ssd_capacity is not None:
        payload["ssd_capacity"] = ssd_capacity
    if new_name is not None:
        payload["new_name"] = new_name
    if auth_required is not None:
        payload["auth_required"] = auth_required
    if not payload:
        typer.echo("Error: at least one update option must be provided", err=True)
        raise typer.Exit(1)
    c = _client(site)
    result = c.put(f"fileSystems/{uid}", json=payload)
    _output(_unwrap(result), as_json=json_output)


# ── Entrypoint ────────────────────────────────────────────────────


def main() -> None:
    app()


if __name__ == "__main__":
    main()
