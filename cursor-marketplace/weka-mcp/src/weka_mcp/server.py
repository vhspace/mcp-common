"""MCP Server for Weka distributed storage.

Exposes Weka REST API v2 as MCP tools for AI assistants.
Covers cluster health, filesystems, snapshots, protocols (S3/NFS/SMB),
and admin operations for both converged and hosted deployments.

Tool design: generic list/get for reads, specific tools for writes.
"""

import argparse
import atexit
import logging
import sys
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp_common import HttpAccessTokenAuth, add_health_route, create_http_app, suppress_ssl_warnings
from mcp_common.agent_remediation import mcp_remediation_wrapper
from mcp_common.logging import setup_logging
from pydantic import Field

from weka_mcp.config import Settings, suppress_noisy_loggers
from weka_mcp.site_manager import SiteManager
from weka_mcp.weka_client import WekaRestClient

logger = logging.getLogger(__name__)

FieldsParam = list[str] | None
LimitParam = Annotated[
    int | None,
    Field(default=None, description="Max items to return. Omit for all."),
]
SiteParam = Annotated[
    str | None,
    Field(default=None, description="Target Weka site. Omit to use the active/default site."),
]

mcp = FastMCP("Weka")
sites = SiteManager()

# ── annotation shortcuts ────────────────────────────────────────

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}

# ── resource type registry ──────────────────────────────────────

_LISTABLE_RESOURCES: dict[str, str] = {
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

_GETTABLE_RESOURCES: dict[str, str] = {
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


# ---------------------------------------------------------------------------
# Health endpoint (via mcp-common)
# ---------------------------------------------------------------------------


async def _weka_health_check() -> dict[str, Any]:
    """Readiness check: verify Weka cluster connectivity."""
    checks: dict[str, Any] = {}
    if sites.active_key:
        try:
            sites.get_client().get("cluster")
            checks["weka_cluster"] = {"status": "ok"}
        except Exception:
            checks["weka_cluster"] = {"status": "error"}
    return checks


add_health_route(mcp, "weka-mcp", health_check_fn=_weka_health_check)


# ── helpers ─────────────────────────────────────────────────────


def _get_client(site: str | None = None) -> WekaRestClient:
    """Return the Weka client for the given site (or the active site)."""
    return sites.get_client(site)


def _ensure_json_serializable(obj: Any) -> Any:
    """Recursively coerce an object into JSON-safe primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ensure_json_serializable(item) for item in obj]
    return str(obj)


def _select_fields(obj: Any, fields: list[str] | None) -> Any:
    """Project dict objects down to a subset of keys to reduce token usage."""
    if not fields:
        return obj
    if isinstance(obj, list):
        return [_select_fields(item, fields) for item in obj]
    if isinstance(obj, dict):
        return {f: obj[f] for f in fields if f in obj}
    return obj


def _apply_limit(data: Any, limit: int | None) -> Any:
    """Truncate a list response to *limit* items."""
    if limit is not None and isinstance(data, list):
        return data[:limit]
    return data


def _safe_result(resp: Any, fields: FieldsParam = None, limit: int | None = None) -> Any:
    """Serialize, unwrap, sanitize, project, and limit a Weka API response."""
    data = _sanitize(_unwrap(_ensure_json_serializable(resp)))
    return _apply_limit(_select_fields(data, fields), limit)


def _summarize_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Count items grouped by a dict key. Used for summary mode."""
    counts: dict[str, int] = {}
    for item in items:
        val = str(item.get(key, "unknown"))
        counts[val] = counts.get(val, 0) + 1
    return counts


def _unwrap(resp: Any) -> Any:
    """Extract data from Weka's ``{"data": [...]}`` / ``{"data": {...}}`` wrapper."""
    if isinstance(resp, dict) and "data" in resp and isinstance(resp["data"], (list, dict)):
        return resp["data"]
    return resp


_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "token",
        "password",
        "secret",
        "jwt",
        "authorization",
        "credentials",
        "api_key",
    }
)


def _sanitize(obj: Any) -> Any:
    """Recursively strip known sensitive keys from response data."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items() if k.lower() not in _SENSITIVE_KEYS}
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


# ── CLI ─────────────────────────────────────────────────────────


def parse_cli_args() -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Weka MCP Server - Model Context Protocol server for Weka storage system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--weka-host", type=str, help="Weka cluster URL (e.g. https://weka01:14000)"
    )
    parser.add_argument("--weka-username", type=str, help="Weka username")
    parser.add_argument("--weka-password", type=str, help="Weka password")
    parser.add_argument("--weka-org", type=str, help="Weka organization (for org-scoped auth)")
    parser.add_argument("--api-base-path", type=str, help="API base path (default: /api/v2)")
    parser.add_argument(
        "--transport", type=str, choices=["stdio", "http"], help="MCP transport (default: stdio)"
    )
    parser.add_argument("--host", type=str, help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="HTTP bind port (default: 8000)")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument(
        "--verify-ssl", action="store_true", dest="verify_ssl", default=None, help="Verify SSL"
    )
    ssl_group.add_argument(
        "--no-verify-ssl", action="store_false", dest="verify_ssl", help="Disable SSL verification"
    )

    parser.add_argument("--timeout-seconds", type=float, help="HTTP timeout (default: 30)")
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()
    return {k: v for k, v in vars(args).items() if v is not None}


# ── MCP tools: read (generic) ──────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_sites() -> list[dict[str, Any]]:
    """List all configured Weka sites with connection details."""
    return sites.list_sites()


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_set_site(site: str) -> dict[str, Any]:
    """Set the active Weka site for subsequent operations.

    Args:
        site: Site name or alias to activate.
    """
    cfg = sites.set_active(site)
    return {"active_site": cfg.site, "weka_host": cfg.weka_host}


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_cluster_overview(site: SiteParam = None) -> dict[str, Any]:
    """START HERE for any Weka investigation. Single-call cluster health overview.

    Returns a compact summary: cluster name, status, IO status, host counts,
    capacity, active MAJOR/CRITICAL alert count, and license info.
    If everything looks healthy, stop. If alerts exist, drill into specifics
    with weka_list_alerts or weka_get_events.

    Args:
        site: Target Weka site. Omit to use the active/default site.
    """
    c = _get_client(site)

    cluster_raw = _sanitize(_unwrap(_ensure_json_serializable(c.get("cluster"))))
    cluster = cluster_raw
    if isinstance(cluster, list) and cluster:
        cluster = cluster[0]

    try:
        alerts_raw = _unwrap(
            _ensure_json_serializable(c.get("alerts", params={"severity": "MAJOR,CRITICAL"}))
        )
        alerts = alerts_raw if isinstance(alerts_raw, list) else []
    except Exception:
        alerts = []

    try:
        lic_raw = _sanitize(_unwrap(_ensure_json_serializable(c.get("license"))))
        lic = lic_raw
        if isinstance(lic, list) and lic:
            lic = lic[0]
    except Exception:
        lic = {}

    summary: dict[str, Any] = {}
    if isinstance(cluster, dict):
        for key in ("name", "guid", "release", "status", "io_status", "init_stage", "hot_spare"):
            if cluster.get(key) is not None:
                summary[key] = cluster[key]
        if isinstance(cluster.get("hosts"), dict):
            summary["hosts"] = cluster["hosts"]
        if isinstance(cluster.get("capacity"), dict):
            summary["capacity"] = cluster["capacity"]
        if isinstance(cluster.get("drives"), dict):
            summary["drives"] = cluster["drives"]
        licensing = cluster.get("licensing")
        if isinstance(licensing, dict):
            summary["licensing_mode"] = licensing.get("mode")

    summary["active_alerts"] = len(alerts)
    if alerts:
        summary["alerts_by_severity"] = _summarize_by(alerts, "severity")
        summary["alert_types"] = _summarize_by(alerts, "type")

    if isinstance(lic, dict):
        for key in (
            "mode",
            "status",
            "expiry_date",
            "licensed_capacity_bytes",
            "used_capacity_bytes",
        ):
            if lic.get(key) is not None:
                summary.setdefault("license", {})[key] = lic[key]

    return summary


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list(
    resource: str,
    fields: FieldsParam = None,
    filters: dict[str, Any] | None = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List Weka resources by type. Use for any read-only inventory query.

    Token-saving tips: always pass `fields` to project only needed keys,
    and `limit` to cap large result sets (drives can return 100+).

    Resource types:
      alerts, alert_types, alert_descriptions, containers, drives, events,
      failure_domains, filesystem_groups, filesystems, interface_groups,
      organizations, processes, s3_buckets, servers, smb_shares,
      snapshot_policies, snapshots, tasks, users

    Args:
        resource: Resource type name (see list above).
        fields: Field names to return (reduces token usage). Example: ["uid","name","status"]
        filters: Query-parameter filters forwarded to the API (resource-specific).
        limit: Max items to return. Use to avoid bloated responses.
        site: Target Weka site. Omit to use the active/default site.
    """
    endpoint = _LISTABLE_RESOURCES.get(resource)
    if endpoint is None:
        raise ToolError(
            f"Unknown resource type '{resource}'. "
            f"Valid types: {', '.join(sorted(_LISTABLE_RESOURCES))}"
        )
    params = {k: v for k, v in (filters or {}).items() if v is not None} or None
    return _safe_result(_get_client(site).get(endpoint, params=params), fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get(
    resource: str,
    uid: str,
    fields: FieldsParam = None,
    site: SiteParam = None,
) -> Any:
    """Get a single Weka resource by UID. Use after listing to drill into one item.

    Resource types: containers, drives, failure_domains, filesystem_groups,
    filesystems, organizations, processes, servers, snapshot_policies,
    snapshots, users

    Args:
        resource: Resource type name.
        uid: Unique identifier (from a prior list call).
        fields: Field names to return (reduces tokens).
        site: Target Weka site. Omit to use the active/default site.
    """
    endpoint = _GETTABLE_RESOURCES.get(resource)
    if endpoint is None:
        raise ToolError(
            f"Unknown resource type '{resource}'. "
            f"Valid types: {', '.join(sorted(_GETTABLE_RESOURCES))}"
        )
    return _safe_result(_get_client(site).get(f"{endpoint}/{uid}"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_events(
    severity: str | None = None,
    category: str | None = None,
    num_results: int | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    fields: FieldsParam = None,
    site: SiteParam = None,
) -> Any:
    """Get cluster events filtered by severity, category, and time range.

    Dedicated tool because events are the primary troubleshooting mechanism.
    Shows what happened and when — use for root-cause analysis.

    Args:
        severity: Filter by severity (INFO, WARNING, MINOR, MAJOR, CRITICAL).
        category: Filter by category (Alerts, Clustering, Drive, IO, Node, etc.).
        num_results: Maximum number of events to return.
        start_time: Start of time window (ISO 8601).
        end_time: End of time window (ISO 8601).
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    if category:
        params["category"] = category
    if num_results is not None:
        params["num_results"] = num_results
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    return _safe_result(_get_client(site).get("events", params=params or None), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_stats(
    realtime: bool = False,
    compact: bool = True,
    fields: FieldsParam = None,
    site: SiteParam = None,
) -> Any:
    """Get cluster performance stats (IOPS, throughput, latency).

    Use realtime=True during active troubleshooting for live numbers.
    The compact flag (default True) strips null/zero values to save tokens.

    Args:
        realtime: True for live stats, False for aggregated historical.
        compact: Strip null/zero values from response (default True).
        fields: Field names to return.
        site: Target Weka site. Omit to use the active/default site.
    """
    endpoint = "stats/realtime" if realtime else "stats"
    data = _ensure_json_serializable(_get_client(site).get(endpoint))
    if compact and isinstance(data, dict):
        data = {k: v for k, v in data.items() if v is not None and v != 0 and v != ""}
    return _select_fields(data, fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_quotas(
    filesystem_uid: str, fields: FieldsParam = None, site: SiteParam = None
) -> Any:
    """List directory quotas for a filesystem.

    Separate from weka_list because it requires a filesystem UID as a path parameter.

    Args:
        filesystem_uid: Filesystem UID to query quotas for.
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get(f"fileSystems/{filesystem_uid}/quota"), fields)


# ── MCP tools: dedicated read shortcuts ─────────────────────────
# Thin wrappers around the generic weka_list/weka_get for AI agent
# discoverability — agents find named tools faster than remembering
# resource-type strings for the generic endpoints.


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_filesystems(
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List all filesystems with capacity, usage, and status.

    Key fields: name, uid, status, total_budget, used_total, available_total,
    group_name. Use fields=["name","uid","status","used_total","total_budget"]
    for a compact capacity overview.

    Args:
        fields: Field names to return per filesystem.
        limit: Max filesystems to return.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("fileSystems"), fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_filesystem(uid: str, fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """Get details for a single filesystem by UID.

    Args:
        uid: Filesystem UID (e.g. from weka_list_filesystems).
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get(f"fileSystems/{uid}"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_fs_groups(fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """List filesystem groups used to organize filesystems.

    Args:
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("fileSystemGroups"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_orgs(fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """List all organizations with quotas and capacity allocation.

    Args:
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("organizations"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_org(uid: str, fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """Get details for a single organization by UID.

    Args:
        uid: Organization UID.
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get(f"organizations/{uid}"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_users(fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """List user accounts. Use when debugging auth/permission issues.

    Gotcha: usernames are globally unique across orgs. Org-scoped users
    can only see filesystems created within their org.

    Args:
        fields: Field names to return per user.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("users"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_cluster_status(fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """Get cluster status including release version, host counts, and IO state.

    Lighter-weight than weka_cluster_overview — returns only the /cluster
    endpoint without alerts or license info.

    Args:
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("cluster"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_nodes(
    summary: bool = False,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List cluster servers (physical/virtual nodes).

    summary=True returns a count by status — useful as a quick health check
    before deciding whether to fetch the full node list.

    Args:
        summary: Return counts by status instead of individual nodes.
        fields: Field names to return per node (ignored when summary=True).
        limit: Max nodes to return (ignored when summary=True).
        site: Target Weka site. Omit to use the active/default site.
    """
    data = _unwrap(_ensure_json_serializable(_get_client(site).get("servers")))
    if summary and isinstance(data, list):
        by_status = _summarize_by(data, "status")
        return {"total_nodes": len(data), "by_status": by_status}
    return _safe_result(data, fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_drives(
    summary: bool = False,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List SSD/NVMe drives. Clusters can have 100+ drives — use summary=True first.

    summary=True returns counts by status instead of individual drive objects,
    saving significant tokens. Use the full list only when investigating a
    specific drive or needing serial numbers.

    Args:
        summary: Return aggregated counts by status instead of all drives.
        fields: Field names to return per drive (ignored when summary=True).
        limit: Max drives to return (ignored when summary=True).
        site: Target Weka site. Omit to use the active/default site.
    """
    data = _unwrap(_ensure_json_serializable(_get_client(site).get("drives")))
    if summary and isinstance(data, list):
        by_status = _summarize_by(data, "status")
        by_host = _summarize_by(data, "hostname")
        return {"total_drives": len(data), "by_status": by_status, "by_host": by_host}
    return _safe_result(data, fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_alerts(
    severity: str | None = None,
    summary: bool = False,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List active cluster alerts. Can return hundreds — use summary=True first.

    summary=True returns counts grouped by type and severity, saving tokens
    when you just need to know the alert landscape. Drill into specifics
    with severity filter or fields projection afterward.

    Args:
        severity: Filter: INFO, WARNING, MINOR, MAJOR, CRITICAL.
        summary: Return counts by type/severity instead of individual alerts.
        fields: Field names to return per alert (ignored when summary=True).
        limit: Max alerts to return (ignored when summary=True).
        site: Target Weka site. Omit to use the active/default site.
    """
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    data = _unwrap(
        _ensure_json_serializable(_get_client(site).get("alerts", params=params or None))
    )
    if summary and isinstance(data, list):
        by_type = _summarize_by(data, "type")
        by_severity = _summarize_by(data, "severity")
        muted = sum(1 for a in data if isinstance(a, dict) and a.get("is_muted"))
        return {
            "total_alerts": len(data),
            "by_type": by_type,
            "by_severity": by_severity,
            "muted": muted,
        }
    return _safe_result(data, fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_events(
    severity: str | None = None,
    num_results: int | None = None,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List recent cluster events — the primary audit/troubleshooting log.

    Events are ordered newest-first. Always pass num_results or limit to
    avoid fetching thousands of events.

    Args:
        severity: Filter: INFO, WARNING, MINOR, MAJOR, CRITICAL.
        num_results: Server-side max events (forwarded to API).
        fields: Field names to return per event.
        limit: Client-side cap applied after fetch.
        site: Target Weka site. Omit to use the active/default site.
    """
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    if num_results is not None:
        params["num_results"] = num_results
    return _safe_result(_get_client(site).get("events", params=params or None), fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_containers(
    summary: bool = False,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List storage containers (nodes/hosts) in the cluster.

    Each container maps to a Weka agent on a host. summary=True returns
    counts by status and mode instead of the full list.

    Args:
        summary: Return counts by status/mode instead of individual containers.
        fields: Field names to return per container (ignored when summary=True).
        limit: Max containers to return (ignored when summary=True).
        site: Target Weka site. Omit to use the active/default site.
    """
    data = _unwrap(_ensure_json_serializable(_get_client(site).get("containers")))
    if summary and isinstance(data, list):
        return {
            "total_containers": len(data),
            "by_status": _summarize_by(data, "status"),
            "by_mode": _summarize_by(data, "mode"),
        }
    return _safe_result(data, fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_processes(
    summary: bool = False,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List running Weka processes (COMPUTE, DRIVES, FRONTEND) across the cluster.

    Process health directly affects IO. On converged clusters, DOWN processes
    indicate storage issues impacting GPU workloads. summary=True returns
    counts by status and type.

    Args:
        summary: Return counts by status/type instead of individual processes.
        fields: Field names to return per process (ignored when summary=True).
        limit: Max processes to return (ignored when summary=True).
        site: Target Weka site. Omit to use the active/default site.
    """
    data = _unwrap(_ensure_json_serializable(_get_client(site).get("processes")))
    if summary and isinstance(data, list):
        return {
            "total_processes": len(data),
            "by_status": _summarize_by(data, "status"),
            "by_type": _summarize_by(data, "type"),
        }
    return _safe_result(data, fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_buckets(fields: FieldsParam = None, site: SiteParam = None) -> Any:
    """List S3 buckets configured on the Weka cluster.

    Args:
        fields: Optional field projection to reduce response size.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).get("s3/buckets"), fields)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_list_snapshots(
    filesystem_uid: str | None = None,
    fields: FieldsParam = None,
    limit: LimitParam = None,
    site: SiteParam = None,
) -> Any:
    """List snapshots, optionally filtered by filesystem UID.

    Args:
        filesystem_uid: Filter to snapshots of this filesystem.
        fields: Field names to return per snapshot.
        limit: Max snapshots to return.
        site: Target Weka site. Omit to use the active/default site.
    """
    params: dict[str, Any] = {}
    if filesystem_uid:
        params["filesystem_uid"] = filesystem_uid
    return _safe_result(_get_client(site).get("snapshots", params=params or None), fields, limit)


@mcp.tool(annotations=_READ_ONLY)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_get_capacity(site: SiteParam = None) -> dict[str, Any]:
    """Cluster-wide capacity summary: total, used, available, and per-filesystem breakdown.

    Combines cluster-level capacity with a filesystem listing to give a
    single-call overview of where storage is allocated and consumed.

    Args:
        site: Target Weka site. Omit to use the active/default site.
    """
    c = _get_client(site)
    cluster = _ensure_json_serializable(c.get("cluster"))
    filesystems = _ensure_json_serializable(c.get("fileSystems"))

    cluster_data = cluster
    if isinstance(cluster, dict) and "data" in cluster:
        inner = cluster["data"]
        if isinstance(inner, list) and inner:
            cluster_data = inner[0]
        elif isinstance(inner, dict):
            cluster_data = inner

    capacity_info: dict[str, Any] = {}
    if isinstance(cluster_data, dict):
        cap = cluster_data.get("capacity")
        if isinstance(cap, dict):
            capacity_info = dict(cap)
        licensing = cluster_data.get("licensing")
        if isinstance(licensing, dict):
            limits = licensing.get("limits", {})
            usage = licensing.get("usage", {})
            if isinstance(limits, dict) and limits.get("usable_capacity_gb") is not None:
                capacity_info["licensed_usable_gb"] = limits["usable_capacity_gb"]
            if isinstance(usage, dict) and usage.get("drive_capacity_gb") is not None:
                capacity_info["drive_capacity_gb"] = usage["drive_capacity_gb"]

    fs_list = filesystems
    if isinstance(filesystems, dict) and "data" in filesystems:
        fs_list = filesystems["data"]

    fs_summary = []
    if isinstance(fs_list, list):
        for fs in fs_list:
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

    return {
        "cluster_capacity": capacity_info,
        "filesystem_count": len(fs_summary),
        "filesystems": fs_summary,
    }


# ── MCP tools: write (specific) ────────────────────────────────


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_manage_alert(
    action: str,
    alert_type: str,
    duration_secs: int | None = None,
    site: SiteParam = None,
) -> Any:
    """Mute or unmute a cluster alert type.

    Args:
        action: "mute" or "unmute".
        alert_type: The alert type identifier (e.g. "NodeDown").
        duration_secs: Mute duration in seconds (required for mute, ignored for unmute).
        site: Target Weka site. Omit to use the active/default site.
    """
    c = _get_client(site)
    if action == "mute":
        if duration_secs is None:
            raise ToolError("duration_secs is required when action is 'mute'")
        return _safe_result(c.put(f"alerts/{alert_type}/mute", json={"expiry": duration_secs}))
    elif action == "unmute":
        return _safe_result(c.put(f"alerts/{alert_type}/unmute"))
    else:
        raise ToolError(f"Invalid action '{action}'. Use 'mute' or 'unmute'.")


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_create_filesystem(
    name: str,
    capacity: str | None = None,
    group_name: str | None = None,
    auth_required: bool = True,
    ssd_capacity: str | None = None,
    total_capacity: str | None = None,
    tiering: dict[str, Any] | None = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Create a new Weka filesystem.

    Args:
        name: Filesystem name (e.g. "training-data").
        capacity: Total capacity with unit (e.g. "10TB", "500GB"). Use this or ssd_capacity+total_capacity.
        group_name: Filesystem group to assign to.
        auth_required: Whether auth is required for mount.
        ssd_capacity: SSD capacity with unit (alternative to capacity).
        total_capacity: Total capacity with unit (use with ssd_capacity).
        tiering: Optional data-tiering configuration dict.
    """
    if capacity is None and total_capacity is None:
        raise ToolError("Provide either capacity or total_capacity")

    _units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}

    def _to_bytes(val: str) -> int:
        v = val.upper().strip()
        for suffix, mult in sorted(_units.items(), key=lambda x: -len(x[0])):
            if v.endswith(suffix):
                return int(float(v[: -len(suffix)]) * mult)
        return int(val)

    payload: dict[str, Any] = {"name": name, "auth_required": auth_required}
    if capacity is not None:
        payload["total_capacity"] = _to_bytes(capacity)
    if ssd_capacity is not None:
        payload["ssd_capacity"] = _to_bytes(ssd_capacity)
    if total_capacity is not None:
        payload["total_capacity"] = _to_bytes(total_capacity)
    if group_name is not None:
        payload["group_name"] = group_name
    if tiering:
        payload["tiering"] = tiering
    return _safe_result(_get_client(site).post("fileSystems", json=payload))


@mcp.tool(annotations=_DESTRUCTIVE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_delete_resource(resource: str, uid: str, site: SiteParam = None) -> Any:
    """Delete a Weka resource by type and UID.

    Supported: filesystems, organizations, snapshots, s3 (no UID needed for s3).

    WARNING: Deleting a filesystem destroys all its data permanently.
    WARNING: Deleting an organization removes all its users and filesystem access.
    WARNING: Deleting a snapshot frees its unique data blocks.

    Args:
        resource: Resource type — "filesystems", "organizations", "snapshots", or "s3".
        uid: Resource UID to delete (ignored for "s3").
        site: Target Weka site. Omit to use the active/default site.
    """
    endpoints = {
        "filesystems": f"fileSystems/{uid}",
        "organizations": f"organizations/{uid}",
        "snapshots": f"snapshots/{uid}",
        "s3": "s3",
    }
    endpoint = endpoints.get(resource)
    if endpoint is None:
        raise ToolError(
            f"Cannot delete resource type '{resource}'. Supported: {', '.join(sorted(endpoints))}"
        )
    return _safe_result(_get_client(site).delete(endpoint))


@mcp.tool(annotations=_DESTRUCTIVE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_delete_filesystem(uid: str, site: SiteParam = None) -> Any:
    """Delete a filesystem by UID. DESTROYS ALL DATA permanently.

    Args:
        uid: Filesystem UID to delete.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).delete(f"fileSystems/{uid}"))


@mcp.tool(annotations=_DESTRUCTIVE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_delete_org(uid: str, site: SiteParam = None) -> Any:
    """Delete an organization by UID. Removes all its users and access.

    Args:
        uid: Organization UID to delete.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).delete(f"organizations/{uid}"))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_create_snapshot(
    filesystem_uid: str,
    name: str,
    is_writable: bool = False,
    access_point: str | None = None,
    site: SiteParam = None,
) -> Any:
    """Create a point-in-time snapshot of a filesystem.

    Read-only snapshots are safer for backups. Writable snapshots allow
    modification for testing but cannot be converted to read-only later.

    Args:
        filesystem_uid: UID of the filesystem to snapshot.
        name: Name for the new snapshot.
        is_writable: Whether the snapshot should be writable (default: False).
        access_point: Optional mount path for the snapshot.
    """
    payload: dict[str, Any] = {
        "filesystem_uid": filesystem_uid,
        "name": name,
        "is_writable": is_writable,
    }
    if access_point:
        payload["access_point"] = access_point
    return _safe_result(_get_client(site).post("snapshots", json=payload))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_upload_snapshot(uid: str, locator: str, site: SiteParam = None) -> Any:
    """Upload a snapshot to object storage (Snap-to-Object).

    Used for backup, disaster recovery, or migrating data between clusters.
    Only read-only snapshots can be uploaded. Upload in chronological order.

    Args:
        uid: Snapshot UID to upload.
        locator: Object-store locator/bucket for the upload destination.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(
        _get_client(site).post(f"snapshots/{uid}/upload", json={"locator": locator})
    )


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_restore_filesystem(
    source_bucket: str,
    snapshot_name: str,
    new_fs_name: str,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Restore a filesystem from a snapshot in an object-store bucket.

    Creates a new filesystem from a previously uploaded snapshot.

    Args:
        source_bucket: Object-store bucket containing the snapshot.
        snapshot_name: Name of the snapshot to restore from.
        new_fs_name: Name for the newly created filesystem.
        site: Target Weka site. Omit to use the active/default site.
    """
    payload = {
        "source_bucket": source_bucket,
        "snapshot_name": snapshot_name,
        "new_fs_name": new_fs_name,
    }
    return _safe_result(_get_client(site).post("fileSystems/download", json=payload))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_manage_s3(
    action: str,
    config: dict[str, Any] | None = None,
    site: SiteParam = None,
) -> Any:
    """Create, update, or delete the Weka S3 cluster.

    Args:
        action: "create", "update", or "delete".
        config: S3 cluster configuration dict (required for create/update, ignored for delete).
        site: Target Weka site. Omit to use the active/default site.
    """
    c = _get_client(site)
    if action == "create":
        if not config:
            raise ToolError("config is required for S3 cluster creation")
        return _safe_result(c.post("s3", json=config))
    elif action == "update":
        if not config:
            raise ToolError("config is required for S3 cluster update")
        return _safe_result(c.put("s3", json=config))
    elif action == "delete":
        return _safe_result(c.delete("s3"))
    else:
        raise ToolError(f"Invalid action '{action}'. Use 'create', 'update', or 'delete'.")


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_create_organization(
    name: str,
    ssd_quota_gb: int,
    total_quota_gb: int,
    username: str | None = None,
    password: str | None = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Create a new Weka organization with SSD and total quotas.

    The Weka API requires an initial admin user. If username/password are
    omitted, defaults to org name and an auto-generated password.

    GOTCHA: To recreate an org's first admin after deletion, you must
    delete the org and re-create it with `weka org create <name> <user> <pass>`.
    Org-scoped users cannot see filesystems created by root org.

    Args:
        name: Organization name.
        ssd_quota_gb: SSD quota in GB.
        total_quota_gb: Total capacity quota in GB.
        username: Initial admin username (defaults to org name).
        password: Initial admin password (auto-generated if omitted).
        site: Target Weka site. Omit to use the active/default site.
    """
    import secrets

    payload = {
        "name": name,
        "username": username or name,
        "password": password or secrets.token_urlsafe(24),
        "ssd_quota": ssd_quota_gb * (1024**3),
        "total_quota": total_quota_gb * (1024**3),
    }
    return _safe_result(_get_client(site).post("organizations", json=payload))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_create_user(
    username: str,
    password: str,
    role: str = "OrgAdmin",
    site: SiteParam = None,
) -> dict[str, Any]:
    """Create a new Weka user in the org of the authenticated session.

    GOTCHAS:
    - Users inherit the LOGIN session's org, not a target org parameter.
      Set WEKA_ORG to the target org before calling.
    - Usernames are globally unique across all orgs.
    - To create the first OrgAdmin for a new org, use weka_create_organization
      (it creates an initial admin user). You cannot add users to an org that
      has zero admins.

    Args:
        username: User login name (globally unique across orgs).
        password: User password.
        role: One of ClusterAdmin, OrgAdmin, Regular, ReadOnly, S3.
        site: Target Weka site. Omit to use the active/default site.
    """
    payload: dict[str, Any] = {
        "username": username,
        "password": password,
        "role": role,
    }
    return _safe_result(_get_client(site).post("users", json=payload))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_create_filesystem_group(
    name: str,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Create a new filesystem group for organizing filesystems.

    Args:
        name: Name for the new filesystem group.
        site: Target Weka site. Omit to use the active/default site.
    """
    return _safe_result(_get_client(site).post("fileSystemGroups", json={"name": name}))


# ── byte-conversion helper ──────────────────────────────────────

_units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}


def _to_bytes(val: str) -> int:
    """Convert a human-readable capacity string (e.g. '520TB') to bytes."""
    v = val.upper().strip()
    for suffix, mult in sorted(_units.items(), key=lambda x: -len(x[0])):
        if v.endswith(suffix):
            return int(float(v[: -len(suffix)]) * mult)
    return int(val)


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_update_org_quota(
    org_uid: str,
    ssd_quota: str | None = None,
    total_quota: str | None = None,
    site: SiteParam = None,
) -> Any:
    """Update SSD and/or total capacity quotas for an organization.

    Accepts human-readable capacity strings (e.g. "520TB", "1PB") and converts
    them to bytes before sending to the Weka API.

    Args:
        org_uid: Organization UID to update.
        ssd_quota: New SSD quota as a capacity string (e.g. "520TB").
        total_quota: New total quota as a capacity string (e.g. "1PB").
        site: Target Weka site. Omit to use the active/default site.
    """
    if ssd_quota is None and total_quota is None:
        raise ToolError("At least one of ssd_quota or total_quota must be provided")
    payload: dict[str, Any] = {}
    if ssd_quota is not None:
        payload["ssd_quota"] = _to_bytes(ssd_quota)
    if total_quota is not None:
        payload["total_quota"] = _to_bytes(total_quota)
    return _safe_result(_get_client(site).put(f"organizations/{org_uid}/limits", json=payload))


@mcp.tool(annotations=_WRITE)
@mcp_remediation_wrapper(project_repo="vhspace/weka-mcp")
def weka_update_filesystem(
    uid: str,
    total_capacity: str | None = None,
    ssd_capacity: str | None = None,
    new_name: str | None = None,
    auth_required: bool | None = None,
    site: SiteParam = None,
) -> Any:
    """Update a filesystem's capacity, name, or auth settings.

    The Weka API accepts total_capacity and ssd_capacity as human-readable
    strings (e.g. "500TB") and parses them server-side.

    Note: non-tiered filesystems will error if ssd_capacity is set separately
    (SSD capacity is deduced as equal to total capacity).

    Args:
        uid: Filesystem UID to update.
        total_capacity: New total capacity as a string (e.g. "500TB").
        ssd_capacity: New SSD capacity as a string (for tiered filesystems only).
        new_name: Rename the filesystem.
        auth_required: Whether authentication is required for mount.
    """
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
        raise ToolError("At least one update field must be provided")
    return _safe_result(_get_client(site).put(f"fileSystems/{uid}", json=payload))


# ---------------------------------------------------------------------------
# Server initialization (shared by CLI and ASGI factory)
# ---------------------------------------------------------------------------

_initialized = False


def _initialize(settings: Settings) -> None:
    """Initialize the Weka site manager from settings. Idempotent."""
    global _initialized
    if _initialized:
        return

    setup_logging(level=settings.log_level, json_output=settings.log_json)
    suppress_noisy_loggers(settings.log_level)

    logger.info("Starting Weka MCP Server")
    logger.info("Effective configuration: %s", settings.get_effective_config_summary())

    if not settings.verify_ssl:
        logger.warning(
            "SSL certificate verification is DISABLED. "
            "This is insecure and should only be used for testing."
        )

    if settings.transport == "http" and settings.host in ["0.0.0.0", "::", "[::]"]:
        logger.warning(
            "HTTP transport is bound to %s:%s, which exposes the service to all "
            "network interfaces. Ensure this is secured with TLS/reverse proxy.",
            settings.host,
            settings.port,
        )

    sites.configure(settings)
    atexit.register(sites.close_all)

    site_list = sites.list_sites()
    logger.info("Configured %d site(s): %s", len(site_list), [s["site"] for s in site_list])
    logger.debug("Active site: %s", sites.active_key)

    _initialized = True


# ---------------------------------------------------------------------------
# ASGI app factory (for uvicorn / K8s deployment)
# ---------------------------------------------------------------------------


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage:
        uvicorn weka_mcp.server:create_app --factory --host 0.0.0.0 --port 8000

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
    """CLI entry point: ``weka-mcp`` command."""
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
            mcp.run(transport="http", host=settings.host, port=settings.port)
    except Exception as e:
        logger.error("Failed to start MCP server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
