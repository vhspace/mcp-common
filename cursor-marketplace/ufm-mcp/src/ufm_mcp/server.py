"""MCP server for NVIDIA UFM (Unified Fabric Manager).

Exposes operational tools for alarms, events, ports, links, logs, and
system dump management across one or more UFM sites.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp_common import (
    HttpAccessTokenAuth,
    add_health_route,
    create_http_app,
    setup_logging,
    suppress_ssl_warnings,
)
from mcp_common.agent_remediation import mcp_remediation_wrapper
from pydantic import Field

from ufm_mcp.config import Settings
from ufm_mcp.helpers import (
    build_guid_to_hostname_map,
    count_severities,
    deduplicate_log_lines,
    ensure_json_serializable,
    get_server_tzinfo,
    is_error_line,
    is_linkish,
    normalize_list_payload,
    parse_sm_log_ts,
    parse_ts_utc,
    parse_ufm_log_ts,
    pkey_diff,
    resolve_pkey_guids_to_hosts,
    summarize_alarm,
    summarize_event,
    top_n,
    truncate_text,
)
from ufm_mcp.site_manager import SiteManager

logger = logging.getLogger(__name__)


def _serializable_dict(obj: Any) -> dict[str, Any]:
    """Coerce *obj* to JSON-serializable form, typed as ``dict`` for mypy strict."""
    return cast(dict[str, Any], ensure_json_serializable(obj))


# JSON Schema / OpenAPI style: unknown fields rejected with this keyword or wording.
_ADDITIONAL_PROPERTIES_KEYWORD = "additionalProperties"
_ADDITIONAL_PROPERTIES_DETAIL_PHRASE = "must NOT have additional properties"


def _http_response_error_detail(response: httpx.Response) -> Any:
    """Parse UFM error body as JSON when possible; otherwise return raw text."""
    try:
        return response.json()
    except Exception:
        return response.text


def _ufm_detail_suggests_additional_properties_rejection(detail: Any) -> bool:
    """True when the error payload indicates rejection of unknown/extra JSON fields.

    UFM (or its validator) may mention the schema keyword ``additionalProperties``,
    or use the common JSON Schema validation message about additional properties.
    We recurse through dicts and lists so nested ``errors`` arrays are handled.
    """
    if isinstance(detail, str):
        if _ADDITIONAL_PROPERTIES_KEYWORD in detail:
            return True
        return _ADDITIONAL_PROPERTIES_DETAIL_PHRASE.lower() in detail.lower()
    if isinstance(detail, dict):
        for key, val in detail.items():
            if _ADDITIONAL_PROPERTIES_KEYWORD in str(key):
                return True
            if _ufm_detail_suggests_additional_properties_rejection(val):
                return True
        return False
    if isinstance(detail, list):
        return any(_ufm_detail_suggests_additional_properties_rejection(item) for item in detail)
    return _ADDITIONAL_PROPERTIES_KEYWORD in str(detail)


_HINT_ADD_HOSTS_FIRST_REQUEST = "Host may not be discovered in UFM yet. Check UFM topology or wait for subnet manager discovery."
_HINT_ADD_HOSTS_GUID_FALLBACK_FAILED = (
    "Both host-based and GUID-based fallback requests failed. "
    "Hosts may not be discovered in UFM yet."
)


def _pkey_body_extras(membership: str, ip_over_ib: bool, index0: bool) -> dict[str, Any]:
    """Build optional pkey body fields, omitting values that match UFM defaults."""
    extras: dict[str, Any] = {}
    if membership != "full":
        extras["membership"] = membership
    if ip_over_ib is not True:
        extras["ip_over_ib"] = ip_over_ib
    if index0 is not False:
        extras["index0"] = index0
    return extras


_INSTRUCTIONS = """\
UFM MCP server for NVIDIA Unified Fabric Manager. Provides operational
tools for InfiniBand fabric monitoring, triage, and partition key management.

Quick triage workflow:
1. ufm_get_cluster_concerns -- one-call summary of alarms, events, logs, BER, links
2. ufm_check_high_ber_recent -- drill into high bit-error-rate ports
3. ufm_check_ports_recent -- investigate specific ports with logs + events
4. ufm_search_logs -- keyword search across UFM/SM logs

Partition key (pkey) management:
1. ufm_list_pkeys / ufm_get_pkey -- inspect configured pkeys and membership
2. ufm_get_pkey_hosts -- resolve pkey GUIDs to hostnames for operator-friendly views
3. ufm_add_guids_to_pkey / ufm_add_hosts_to_pkey -- add ports or hosts to a pkey
4. ufm_remove_guids_from_pkey / ufm_remove_hosts_from_pkey -- remove ports or hosts

Multi-site: use ufm_list_sites to see available sites, then pass site= to any tool.
Write operations (system dumps, log history) require allow_write=true.
"""

mcp = FastMCP("UFM", instructions=_INSTRUCTIONS)
sites = SiteManager()
_base_settings: Settings | None = None


async def _ufm_health_check() -> dict[str, Any]:
    """Readiness check: verify UFM connectivity for active site."""
    checks: dict[str, Any] = {}
    try:
        cfg = sites.get_config(None)
        client = sites.get_client(None)
        result = client.get_json(f"{cfg.ufm_api_base_path}/app/ufm_version")
        if result:
            checks["ufm_api"] = {"status": "ok"}
        else:
            checks["ufm_api"] = {"status": "error"}
    except Exception:
        checks["ufm_api"] = {"status": "error"}
    return checks


add_health_route(mcp, "ufm-mcp", health_check_fn=_ufm_health_check)

# ---------- Reusable annotated types ----------

SiteParam = Annotated[
    str | None,
    Field(
        default=None,
        description="Target site key or alias (e.g. oh1, md1, 5c_oh1). Omit for active site.",
    ),
]
SeverityParam = Annotated[
    str | None,
    Field(default=None, description="UFM severity filter (e.g., Warning, Critical, Info)"),
]
LogType = Literal["Event", "SM", "UFM"]
SystemDumpMode = Literal["Default", "SnapShot"]
HighBerSeverityFilter = Literal["warning", "error"]
MembershipType = Literal["full", "limited"]

# ---------- CLI parsing ----------


def _parse_cli_args() -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="ufm-mcp - Model Context Protocol server for NVIDIA UFM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ufm-url", type=str, help="Base URL of UFM")
    parser.add_argument("--ufm-token", type=str, help="UFM access token")
    parser.add_argument("--transport", type=str, choices=["stdio", "http"])
    parser.add_argument("--host", type=str, help="Host for HTTP server")
    parser.add_argument("--port", type=int, help="Port for HTTP server")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument("--verify-ssl", action="store_true", dest="verify_ssl", default=None)
    ssl_group.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")

    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument(
        "--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )

    args = parser.parse_args()
    overlay: dict[str, Any] = {}
    for key in (
        "ufm_url",
        "ufm_token",
        "transport",
        "host",
        "port",
        "verify_ssl",
        "timeout_seconds",
        "log_level",
    ):
        val = getattr(args, key, None)
        if val is not None:
            overlay[key] = val
    return overlay


# ================================================================
#  TOOLS: Site management
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_sites() -> dict[str, Any]:
    """List configured UFM sites and aliases for this MCP instance."""
    return {
        "ok": True,
        "active_site": sites.active_key,
        "sites": sites.list_sites(),
        "aliases": sites.aliases,
    }


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_set_site(
    site: Annotated[str, Field(description="Site key or alias to activate")],
) -> dict[str, Any]:
    """Set the active UFM site for subsequent tool calls."""
    cfg = sites.set_active(site)
    return {
        "ok": True,
        "active_site": cfg.site,
        "ufm_url": cfg.ufm_url,
        "verify_ssl": cfg.verify_ssl,
        "timeout_seconds": cfg.timeout_seconds,
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_config(site: SiteParam = None) -> dict[str, Any]:
    """Return effective ufm-mcp configuration (secrets redacted)."""
    if _base_settings is None:
        return {"ok": False, "error": "settings not initialized"}
    cfg = sites.get_config(site)
    summary = _base_settings.get_effective_config_summary()
    summary.update(sites.get_effective_summary())
    summary["resolved_site"] = cfg.site
    summary["resolved_ufm_url"] = cfg.ufm_url
    return {"ok": True, "config": summary}


# ================================================================
#  TOOLS: Version / health
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_version(site: SiteParam = None) -> dict[str, Any]:
    """Get UFM version. Endpoint: GET <api_base>/app/ufm_version"""
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    result = client.get_json(f"{cfg.ufm_api_base_path}/app/ufm_version")
    if isinstance(result, str):
        return {"ok": True, "version": result}
    return {"ok": True, "version": ensure_json_serializable(result)}


# ================================================================
#  TOOLS: Alarms
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_alarms(
    alarm_id: Annotated[
        int | None, Field(default=None, ge=1, description="Alarm ID to fetch")
    ] = None,
    device_id: Annotated[
        str | None, Field(default=None, description="Filter alarms by device_id")
    ] = None,
    resolve_names: Annotated[
        bool, Field(default=True, description="Resolve object GUIDs to hostnames")
    ] = True,
    limit: Annotated[int, Field(default=200, ge=1, le=2000)] = 200,
    site: SiteParam = None,
) -> dict[str, Any]:
    """List active UFM alarms or fetch a specific alarm by ID.

    Alarms represent persistent conditions (e.g. link down, high BER) that
    remain until the condition clears. Each alarm has a severity
    (Info/Warning/Minor/Major/Critical) and an object_name identifying the
    affected switch port or device. When resolve_names is true, object GUIDs
    are resolved to human-readable hostnames via the systems endpoint.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_api_base_path

    if alarm_id is not None:
        return _serializable_dict(client.get_json(f"{base}/app/alarms/{alarm_id}"))

    params: dict[str, Any] = {}
    if device_id:
        params["device_id"] = device_id

    alarms = client.get_json(f"{base}/app/alarms", params=params)
    if isinstance(alarms, list):
        alarms = alarms[:limit]
        if resolve_names:
            _resolve_alarm_object_names(client, cfg.ufm_resources_base_path, alarms)
        return {"ok": True, "count": len(alarms), "alarms": ensure_json_serializable(alarms)}
    return _serializable_dict(alarms)


_systems_cache: dict[str, tuple[float, dict[str, str]]] = {}
_SYSTEMS_CACHE_TTL = 120  # seconds


def _get_guid_map(client: Any, resources_base: str) -> dict[str, str]:
    """Return a GUID→hostname map, cached per resources_base for 120s."""
    now = time.monotonic()
    cached = _systems_cache.get(resources_base)
    if cached and (now - cached[0]) < _SYSTEMS_CACHE_TTL:
        return cached[1]
    systems = client.get_json(f"{resources_base}/resources/systems")
    guid_map = build_guid_to_hostname_map(systems if isinstance(systems, list) else [])
    _systems_cache[resources_base] = (now, guid_map)
    return guid_map


def _resolve_alarm_object_names(client: Any, resources_base: str, alarms: list[Any]) -> None:
    """Resolve object_name GUIDs to hostnames in-place."""
    guid_map = _get_guid_map(client, resources_base)
    for a in alarms:
        if not isinstance(a, dict):
            continue
        obj = str(a.get("object_name") or "").strip()
        if not obj:
            continue
        parts = obj.split("_")
        guid_part = parts[0].lower() if parts else obj.lower()
        resolved = guid_map.get(guid_part) or guid_map.get(obj.lower())
        if resolved:
            a["resolved_name"] = resolved


# ================================================================
#  TOOLS: Unhealthy ports
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_unhealthy_ports(site: SiteParam = None) -> dict[str, Any]:
    """List InfiniBand ports currently flagged as unhealthy by UFM.

    Unhealthy ports are those UFM has isolated or marked due to persistent
    errors (e.g. high bit-error rate, link flapping). These ports may be
    excluded from fabric routing until manually re-enabled.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    raw = client.get_json(f"{cfg.ufm_api_base_path}/app/unhealthy_ports")
    ports = raw if isinstance(raw, list) else []
    return _serializable_dict(
        {
            "ok": True,
            "count": len(ports),
            "unhealthy_ports": ensure_json_serializable(ports),
            "site": cfg.site,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_unhealthy_ports_policy(site: SiteParam = None) -> dict[str, Any]:
    """Get the UFM unhealthy-ports policy configuration.

    Returns the rules UFM uses to decide when to mark a port unhealthy
    (e.g. BER thresholds, symbol-error counts, link-down frequency).
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    return _serializable_dict(
        client.get_json(f"{cfg.ufm_api_base_path}/app/unhealthy_ports/policy")
    )


# ================================================================
#  TOOLS: Switches
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_switches(
    site: SiteParam = None,
    errors_only: Annotated[
        bool, Field(default=False, description="Only return switches with non-Info severity")
    ] = False,
) -> dict[str, Any]:
    """List all InfiniBand switches managed by UFM with health summary.

    Returns switch name, GUID, model, state, severity, and total port count.
    Use errors_only=True to filter to switches with non-Info severity.
    Query: GET /resources/systems?type=switch
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    systems = client.get_json(
        f"{cfg.ufm_resources_base_path}/resources/systems", params={"type": "switch"}
    )
    if not isinstance(systems, list):
        systems = []

    switches: list[dict[str, Any]] = []
    for s in systems:
        if not isinstance(s, dict):
            continue
        sev = str(s.get("severity", "")).strip()
        if errors_only and sev.lower() in ("", "info"):
            continue
        total_ports = 0
        ports = s.get("ports")
        if isinstance(ports, list):
            total_ports = len(ports)
        elif isinstance(ports, int):
            total_ports = ports
        switches.append(
            {
                "system_name": s.get("system_name"),
                "guid": s.get("guid"),
                "model": s.get("model"),
                "vendor": s.get("vendor"),
                "state": s.get("state"),
                "severity": sev or "Unknown",
                "technology": s.get("technology"),
                "total_ports": total_ports,
            }
        )

    sev_counts = count_severities(switches)
    return _serializable_dict(
        {
            "ok": True,
            "count": len(switches),
            "severity_counts": sev_counts,
            "switches": switches,
            "site": cfg.site,
        }
    )


# ================================================================
#  TOOLS: Events
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_events(
    event_id: Annotated[
        int | None, Field(default=None, ge=1, description="Event ID to fetch")
    ] = None,
    severity: SeverityParam = None,
    group: Annotated[str | None, Field(default=None, description="Filter events by group")] = None,
    limit: Annotated[int, Field(default=200, ge=1, le=5000)] = 200,
    site: SiteParam = None,
) -> dict[str, Any]:
    """List UFM events or fetch a specific event by ID.

    Events are point-in-time occurrences (e.g. link state change,
    port error threshold exceeded). Unlike alarms, events are historical
    and have timestamps. Filter by severity (Info/Warning/Critical) or
    group (e.g. Fabric, Threshold).
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_api_base_path

    if event_id is not None:
        return _serializable_dict(client.get_json(f"{base}/app/events/{event_id}"))

    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    if group:
        params["group"] = group

    events = client.get_json(f"{base}/app/events", params=params)
    if isinstance(events, list):
        events = events[:limit]
        return {"ok": True, "count": len(events), "events": ensure_json_serializable(events)}
    return _serializable_dict(events)


# ================================================================
#  TOOLS: Concerns (quick triage)
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_concerns(
    include_events: Annotated[bool, Field(default=True)] = True,
    include_alarms: Annotated[bool, Field(default=True)] = True,
    max_items: Annotated[int, Field(default=200, ge=1, le=2000)] = 200,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Return a summary of current warnings/errors/concerns.

    Alarms: include those with severity != "Info".
    Events: include those with severity in Warning/Error/Critical/Major/Minor.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_api_base_path

    concerns: dict[str, Any] = {"ok": True}
    severities_bad = {"warning", "error", "critical", "major", "minor"}

    if include_alarms:
        alarms = client.get_json(f"{base}/app/alarms")
        if isinstance(alarms, list):
            filtered = [a for a in alarms if str(a.get("severity", "")).lower() != "info"][
                :max_items
            ]
            concerns["alarms"] = filtered
            concerns["alarms_count"] = len(filtered)
        else:
            concerns["alarms_error"] = "Unexpected alarms payload (not a list)"

    if include_events:
        events = client.get_json(f"{base}/app/events")
        if isinstance(events, list):
            filtered = [e for e in events if str(e.get("severity", "")).lower() in severities_bad][
                :max_items
            ]
            concerns["events"] = filtered
            concerns["events_count"] = len(filtered)
        else:
            concerns["events_error"] = "Unexpected events payload (not a list)"

    summary: dict[str, int] = {}
    for k in ["alarms", "events"]:
        items = concerns.get(k)
        if isinstance(items, list):
            for it in items:
                sev = str((it or {}).get("severity", "")).strip() or "Unknown"
                summary[sev] = summary.get(sev, 0) + 1
    concerns["severity_summary"] = summary

    return _serializable_dict(concerns)


# ================================================================
#  TOOLS: High BER ports
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_high_ber_ports(
    severity: Annotated[
        HighBerSeverityFilter | None,
        Field(
            default=None,
            description="Filter: 'warning' or 'error'. UFM returns 'Warning'/'Critical'; query uses 'warning'/'error'.",
        ),
    ] = None,
    include_cable_info: Annotated[
        bool, Field(default=False, description="Request cable/transceiver info")
    ] = False,
    limit: Annotated[int, Field(default=200, ge=1, le=5000)] = 200,
    fields: Annotated[
        list[str] | None,
        Field(default=None, description="Fields to return per port (reduces token usage)."),
    ] = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """List ports with high BER (Bit Error Rate) — a key InfiniBand health metric.

    BER measures the fraction of corrupted bits on a link. UFM flags ports
    exceeding configurable thresholds as 'warning' or 'error' severity.
    High-BER ports often indicate cable, transceiver, or connector issues.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path

    params: dict[str, Any] = {"high_ber_only": "true"}
    if severity is not None:
        params["high_ber_severity"] = severity
    if include_cable_info:
        params["cable_info"] = "true"

    ports_payload = client.get_json(f"{base}/resources/ports", params=params)
    port_list = normalize_list_payload(ports_payload)

    if not port_list and not isinstance(ports_payload, (list, dict)):
        return _serializable_dict(
            {"ok": False, "error": "Unexpected ports payload shape", "response": ports_payload}
        )

    if fields:
        port_list = [{k: p.get(k) for k in fields if k in p} for p in port_list]

    port_list = port_list[:limit]

    return _serializable_dict(
        {
            "ok": True,
            "count": len(port_list),
            "severity_filter": severity,
            "include_cable_info": include_cable_info,
            "severity_summary": count_severities(port_list, key="high_ber_severity"),
            "ports": port_list,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_check_high_ber_recent(
    lookback_minutes: Annotated[int, Field(default=30, ge=1, le=1440)] = 30,
    max_ports: Annotated[int, Field(default=20, ge=1, le=200)] = 20,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Summarize high-BER (Bit Error Rate) ports with recent alarm/event activity.

    Combines the current high-BER port list with recent alarms and events
    to show which problematic ports are actively generating errors within
    the lookback window. Use this to prioritize which ports need attention.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path
    api_base = cfg.ufm_api_base_path

    now_utc = datetime.now(UTC)
    cutoff_utc = now_utc - timedelta(minutes=lookback_minutes)

    ports = normalize_list_payload(
        client.get_json(f"{resources_base}/resources/ports", params={"high_ber_only": "true"})
    )
    if not ports:
        return {
            "ok": True,
            "high_ber_ports_current_count": 0,
            "note": "No high-BER ports found or unexpected payload.",
        }

    ber_sev_counts = count_severities(ports, key="high_ber_severity")
    ber_ports_by_name: dict[str, dict[str, Any]] = {}
    for p in ports:
        name = p.get("name")
        if isinstance(name, str) and name.strip():
            ber_ports_by_name[name.strip()] = p
    ber_names = set(ber_ports_by_name.keys())

    events_payload = client.get_json(f"{api_base}/app/events")
    alarms_payload = client.get_json(f"{api_base}/app/alarms")

    recent_event_name_counts: Counter[str] = Counter()
    recent_event_sev_counts: Counter[str] = Counter()
    events_by_port: Counter[str] = Counter()

    if isinstance(events_payload, list):
        for e in events_payload:
            if not isinstance(e, dict):
                continue
            obj = str(e.get("object_name") or "").strip()
            if obj not in ber_names:
                continue
            dt = parse_ts_utc(str(e.get("timestamp") or ""))
            if dt is None or dt < cutoff_utc:
                continue
            recent_event_name_counts[str(e.get("name") or "Unknown")] += 1
            recent_event_sev_counts[str(e.get("severity") or "Unknown")] += 1
            events_by_port[obj] += 1

    recent_alarm_name_counts: Counter[str] = Counter()
    recent_alarm_desc_counts: Counter[str] = Counter()
    alarms_by_port: Counter[str] = Counter()

    if isinstance(alarms_payload, list):
        for a in alarms_payload:
            if not isinstance(a, dict):
                continue
            obj = str(a.get("object_name") or "").strip()
            if obj not in ber_names:
                continue
            dt = parse_ts_utc(str(a.get("timestamp") or ""))
            if dt is not None and dt < cutoff_utc:
                continue
            recent_alarm_name_counts[str(a.get("name") or "Unknown")] += 1
            recent_alarm_desc_counts[str(a.get("description") or "Unknown")] += 1
            alarms_by_port[obj] += 1

    active_ports = set(events_by_port.keys()) | set(alarms_by_port.keys())

    top_ports = sorted(events_by_port.items(), key=lambda kv: kv[1], reverse=True)[:max_ports]
    top_ports_out = []
    for port_name, count in top_ports:
        p = ber_ports_by_name.get(port_name, {})
        top_ports_out.append(
            {
                "port": port_name,
                "system_name": p.get("system_name"),
                "dname": p.get("dname"),
                "high_ber_severity": p.get("high_ber_severity"),
                "event_count": count,
                "alarm_count": alarms_by_port.get(port_name, 0),
            }
        )

    return _serializable_dict(
        {
            "ok": True,
            "now_utc": now_utc.isoformat(),
            "cutoff_utc": cutoff_utc.isoformat(),
            "lookback_minutes": lookback_minutes,
            "high_ber_ports_current_count": len(ber_names),
            "high_ber_ports_severity_counts": ber_sev_counts,
            "high_ber_ports_with_recent_activity": len(active_ports),
            "recent_events_on_high_ber_ports_count": sum(events_by_port.values()),
            "recent_events_severity_counts": dict(recent_event_sev_counts),
            "top_recent_event_names": top_n(dict(recent_event_name_counts)),
            "recent_alarms_on_high_ber_ports_count": sum(alarms_by_port.values()),
            "top_recent_alarm_names": top_n(dict(recent_alarm_name_counts)),
            "top_recent_alarm_descriptions": top_n(dict(recent_alarm_desc_counts)),
            "top_ports_by_recent_events": top_ports_out,
            "note": "'Recent' is inferred from current events/alarms feeds; if truncated, activity may be under-reported.",
        }
    )


# ================================================================
#  TOOLS: Port health
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_ports_health(
    system: Annotated[
        str,
        Field(
            default="",
            description=(
                "System name or GUID (e.g. hci-oh1-ibs11 or fc6a1c0300b2ed00). "
                "Omit when using port_guid or node_guid."
            ),
        ),
    ] = "",
    port_numbers: Annotated[
        list[int] | None,
        Field(
            default=None,
            description="Port numbers to inspect (e.g. [63, 64]). Omit to list all ports.",
        ),
    ] = None,
    include_peer_ports: Annotated[
        bool, Field(default=True, description="Include peer port summaries")
    ] = True,
    include_alarms: Annotated[
        bool, Field(default=True, description="Include matching current alarms")
    ] = True,
    include_cable_info: Annotated[
        bool, Field(default=False, description="Request cable/transceiver info")
    ] = False,
    errors_only: Annotated[
        bool, Field(default=False, description="Only return ports with non-Info severity or alarms")
    ] = False,
    down_only: Annotated[
        bool,
        Field(default=False, description="Only return ports whose physical_state is not Active"),
    ] = False,
    port_guid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Port GUID (e.g. 0xa088c20300556b96 from `ibstat`). "
                "Bypasses system resolution. Provide exactly one of: system, port_guid, node_guid."
            ),
        ),
    ] = None,
    node_guid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "HCA/system node GUID; bypasses /resources/systems lookup entirely. "
                "Provide exactly one of: system, port_guid, node_guid."
            ),
        ),
    ] = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Get detailed health for specific ports on an InfiniBand switch or HCA.

    Returns physical/logical state, active speed/width, BER severity, and
    peer port info. Provide exactly one selector:
      - system: switch/HCA name or GUID (e.g. hci-oh1-ibs11)
      - port_guid: pivot directly from `ibstat` Port GUID (bypasses system lookup)
      - node_guid: HCA/system node GUID (bypasses /resources/systems lookup)

    Port numbers are the UFM port indices on that system. Omit port_numbers to list all ports.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path
    api_base = cfg.ufm_api_base_path

    selectors_provided = sum(bool(x) for x in (system, port_guid, node_guid))
    if selectors_provided != 1:
        raise ToolError("Provide exactly one of: system, port_guid, node_guid")

    if port_guid:
        # UFM does not support ?guid=<port_guid> server-side (returns HTTP 400 on
        # all tested versions). Fetch unfiltered and filter client-side.
        _pg_params: dict[str, str] = {"cable_info": "true"} if include_cable_info else {}
        _pg_unfiltered = normalize_list_payload(
            client.get_json(f"{resources_base}/resources/ports", params=_pg_params or None)
        )
        records = [p for p in _pg_unfiltered if str(p.get("guid", "")) == port_guid]
        if not records:
            return _serializable_dict(
                {
                    "ok": False,
                    "error": f"No ports matched port_guid={port_guid!r}",
                    "inventory_source": "port_guid_query",
                }
            )
        return _ports_health_from_records(
            records,
            client=client,
            resources_base=resources_base,
            api_base=api_base,
            port_numbers=port_numbers,
            include_peer_ports=include_peer_ports,
            include_alarms=include_alarms,
            errors_only=errors_only,
            down_only=down_only,
            inventory_source="port_guid_query",
        )

    if node_guid:
        params = {"system": node_guid}
        if include_cable_info:
            params["cable_info"] = "true"
        records = normalize_list_payload(
            client.get_json(f"{resources_base}/resources/ports", params=params)
        )
        if not records:
            return _serializable_dict(
                {
                    "ok": False,
                    "error": f"No ports matched node_guid={node_guid!r}",
                    "inventory_source": "node_guid_query",
                }
            )
        return _ports_health_from_records(
            records,
            client=client,
            resources_base=resources_base,
            api_base=api_base,
            port_numbers=port_numbers,
            include_peer_ports=include_peer_ports,
            include_alarms=include_alarms,
            errors_only=errors_only,
            down_only=down_only,
            inventory_source="node_guid_query",
        )

    # --- system-name / system-GUID path ---
    system_query = system.strip()
    systems = client.get_json(f"{resources_base}/resources/systems")
    if not isinstance(systems, list):
        raise ToolError("Unexpected systems payload (not a list)")

    system_obj = _find_system(systems, system_query)
    if system_obj is None:
        candidates = [
            {"system_name": str(s.get("system_name", "")), "guid": s.get("guid")}
            for s in systems
            if isinstance(s, dict) and system_query.lower() in str(s.get("system_name", "")).lower()
        ][:20]
        return _serializable_dict(
            {"ok": False, "error": f"System not found: {system_query!r}", "candidates": candidates}
        )

    system_guid = str(
        system_obj.get("guid") or system_obj.get("system_guid") or system_obj.get("name") or ""
    ).strip()
    if not system_guid:
        raise ToolError("Unable to resolve system GUID")

    expected_port_count = len(system_obj.get("ports") or [])
    inventory_warnings: dict[str, Any] | None = None

    cable_param: dict[str, str] = {"cable_info": "true"} if include_cable_info else {}

    guid_params: dict[str, Any] = {"system": system_guid, **cable_param}
    guid_ports = normalize_list_payload(
        client.get_json(f"{resources_base}/resources/ports", params=guid_params)
    )

    all_ports = guid_ports
    stale_anchor = expected_port_count > 0 and len(guid_ports) < expected_port_count

    if stale_anchor:
        sys_name = str(system_obj.get("system_name") or system_obj.get("name") or "").strip()
        # If both system_name and name are absent on the system record (malformed
        # UFM data), we have no value to fall back on — keep guid_ports as the
        # result and skip the inventory_warnings annotation. The caller still gets
        # whatever ports the GUID query returned.
        if sys_name:
            # UFM does not support ?system_name= server-side (returns HTTP 400 on all
            # tested versions despite older docs claiming otherwise). Fetch unfiltered
            # and filter client-side. Cost: one ~150KB JSON payload on a ~4000-port fabric.
            unfiltered = normalize_list_payload(
                client.get_json(f"{resources_base}/resources/ports", params=cable_param or None)
            )
            name_ports = [p for p in unfiltered if str(p.get("system_name", "")) == sys_name]
            if len(name_ports) > len(guid_ports):
                all_ports = name_ports
                ghost_names = {str(p.get("name")) for p in guid_ports} - {
                    str(p.get("name")) for p in name_ports
                }
                inventory_warnings = {
                    "stale_anchor_detected": True,
                    "anchor_guid": system_guid,
                    "system_name": sys_name,
                    "ports_by_guid": len(guid_ports),
                    "ports_by_name": len(name_ports),
                    "record_ports": expected_port_count,
                    "anchor_only_port_names": sorted(ghost_names),
                    "remediation_hint": (
                        "UFM's anchor GUID for this system does not match all current ports. "
                        "If post-HCA-swap, see skills/ufm-stale-inventory-recovery/SKILL.md "
                        "(or run `ufm-cli inventory-doctor <system>` for a full breakdown)."
                    ),
                }

    return _ports_health_from_records(
        all_ports,
        client=client,
        resources_base=resources_base,
        api_base=api_base,
        port_numbers=port_numbers,
        include_peer_ports=include_peer_ports,
        include_alarms=include_alarms,
        errors_only=errors_only,
        down_only=down_only,
        system_obj=system_obj,
        inventory_warnings=inventory_warnings,
    )


def _ports_health_from_records(
    ports: list[dict[str, Any]],
    *,
    client: Any,
    resources_base: str,
    api_base: str,
    port_numbers: list[int] | None,
    include_peer_ports: bool,
    include_alarms: bool,
    errors_only: bool,
    down_only: bool,
    inventory_source: str | None = None,
    system_obj: dict[str, Any] | None = None,
    inventory_warnings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ufm_get_ports_health response dict from a list of port records.

    Used by the system-name path (with `system_obj` and possibly `inventory_warnings`)
    and by the port_guid / node_guid sidedoors (with `inventory_source`).
    """
    ports_by_number: dict[int, dict[str, Any]] = {}
    for p in ports:
        try:
            ports_by_number[int(p["number"])] = p
        except (TypeError, ValueError):
            continue

    if port_numbers is not None:
        port_set = {int(p) for p in port_numbers}
        selected_ports: list[dict[str, Any]] = []
        missing_ports: list[int] = []
        for num in sorted(port_set):
            if num in ports_by_number:
                selected_ports.append(ports_by_number[num])
            else:
                missing_ports.append(num)
    else:
        selected_ports = [ports_by_number[n] for n in sorted(ports_by_number)]
        missing_ports = []

    if down_only:
        selected_ports = [
            p for p in selected_ports if str(p.get("physical_state", "")).lower() != "active"
        ]
    if errors_only:
        selected_ports = [
            p
            for p in selected_ports
            if str(p.get("severity", "")).strip().lower() not in ("", "info")
            or str(p.get("high_ber_severity", "")).strip() != ""
        ]

    peer_summaries = (
        _resolve_peer_summaries(client, resources_base, selected_ports)
        if include_peer_ports
        else {}
    )
    alarms_by_object = (
        _collect_port_alarms(client, api_base, selected_ports, include_peer_ports)
        if include_alarms
        else {}
    )

    def port_summary(p: dict[str, Any]) -> dict[str, Any]:
        peer_key = None
        peer_guid = str(p.get("peer_guid") or p.get("peer_node_guid") or "").strip()
        peer_dname = str(p.get("peer_port_dname") or "").strip()
        if include_peer_ports and peer_guid and peer_dname:
            peer_key = f"{peer_guid}_{peer_dname}"

        name = p.get("name")
        alarm_keys = [str(name)] if isinstance(name, str) else []
        if include_peer_ports and isinstance(p.get("peer"), str):
            alarm_keys.append(p["peer"])

        return {
            "system_name": p.get("system_name"),
            "system_guid": p.get("systemID"),
            "name": name,
            "number": p.get("number"),
            "dname": p.get("dname"),
            "physical_state": p.get("physical_state"),
            "logical_state": p.get("logical_state"),
            "severity": p.get("severity"),
            "high_ber_severity": p.get("high_ber_severity"),
            "active_speed": p.get("active_speed"),
            "active_width": p.get("active_width"),
            "fec_mode": p.get("fec_mode"),
            "effective_ber": p.get("effective_ber"),
            "fec_uncorrectable": p.get("port_fec_uncorrectable_block_counter"),
            "fec_correctable": p.get("port_fec_correctable_block_counter"),
            "symbol_error_counter": p.get("symbol_error_counter"),
            "link_down_counter": p.get("link_down_counter"),
            "remote_guid": p.get("remote_guid"),
            "remote_node_desc": p.get("remote_node_desc"),
            "remote_lid": p.get("remote_lid"),
            "peer_node_name": p.get("peer_node_name"),
            "peer_port_dname": p.get("peer_port_dname"),
            "peer_guid": p.get("peer_guid"),
            "peer": p.get("peer"),
            "path": p.get("path"),
            "peer_port": peer_summaries.get(peer_key) if peer_key else None,
            "alarms": [alarm for k in alarm_keys for alarm in alarms_by_object.get(k, [])]
            if include_alarms
            else [],
        }

    if system_obj is not None:
        system_guid = str(
            system_obj.get("guid") or system_obj.get("system_guid") or system_obj.get("name") or ""
        ).strip()
        system_block: dict[str, Any] = {
            "system_name": system_obj.get("system_name"),
            "guid": system_guid,
            "model": system_obj.get("model"),
            "vendor": system_obj.get("vendor"),
            "severity": system_obj.get("severity"),
            "state": system_obj.get("state"),
            "technology": system_obj.get("technology"),
        }
    else:
        # Sidedoor path: derive system identity from the port records themselves.
        first = ports[0] if ports else {}
        system_block = {
            "system_name": first.get("system_name"),
            "guid": str(first.get("systemID") or first.get("system_guid") or ""),
            "model": None,
            "vendor": None,
            "severity": None,
            "state": None,
            "technology": None,
        }

    response: dict[str, Any] = {
        "ok": True,
        "system": system_block,
        "ports": [port_summary(p) for p in selected_ports],
        "missing_ports": missing_ports,
    }
    if inventory_source is not None:
        response["inventory_source"] = inventory_source
    if inventory_warnings is not None:
        response["inventory_warnings"] = inventory_warnings
    return _serializable_dict(response)


def _find_system(systems: list[Any], query: str) -> dict[str, Any] | None:
    for s in systems:
        if not isinstance(s, dict):
            continue
        if query in {
            str(s.get("guid", "")),
            str(s.get("system_guid", "")),
            str(s.get("name", "")),
            str(s.get("system_name", "")),
        }:
            return s
    for s in systems:
        if not isinstance(s, dict):
            continue
        if str(s.get("system_name", "")).lower() == query.lower():
            return s
    return None


# ================================================================
#  TOOLS: Inventory doctor
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_inventory_doctor(
    system: Annotated[str, Field(description="System name to diagnose")],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Diagnose stale-anchor / ghost-port drift for a UFM system.

    Cross-checks three sources of port inventory for a system name:
      1. system_record — /resources/systems entry (anchor GUID + declared ports)
      2. ports_by_name — /resources/ports unfiltered, filtered client-side by system_name
      3. ports_by_guid — same unfiltered fetch, filtered client-side by systemID == anchor_guid

    Returns an inferred_diagnosis of: clean, stale_anchor, ghost_ports,
    host_node_desc_missing, record_undercount, empty_system, or unknown —
    plus a remediation_hint.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path

    sys_name = system.strip()

    # 1. Fetch system record.
    systems = normalize_list_payload(client.get_json(f"{resources_base}/resources/systems"))
    system_record = _find_system(systems, sys_name)
    if system_record is None:
        return _serializable_dict({"ok": False, "error": f"System not found: {system!r}"})

    anchor_guid = str(system_record.get("guid") or system_record.get("system_guid") or "")

    # 2 & 3. Fetch all ports once (UFM does not support ?system_name= server-side —
    # returns HTTP 400 on all tested versions). Derive both views client-side from
    # a single unfiltered payload, which also keeps the two views self-consistent.
    all_ports = normalize_list_payload(client.get_json(f"{resources_base}/resources/ports"))
    ports_by_name = [p for p in all_ports if str(p.get("system_name", "")) == sys_name]
    ports_by_guid = [
        p
        for p in all_ports
        if str(p.get("systemID", "")) == anchor_guid or str(p.get("system_guid", "")) == anchor_guid
    ]

    # Build name sets for reconciliation.
    record_ports_raw = system_record.get("ports") or []
    record_port_names: set[str] = set()
    for p in record_ports_raw:
        if isinstance(p, str):
            # Real UFM format: port-name strings like "0xa088c20300556b96_3".
            if p:
                record_port_names.add(p)
        elif isinstance(p, dict):
            # Test fixtures (and any hypothetical structured variant): {"name": ..., "number": ...}.
            name_val = p.get("name")
            if isinstance(name_val, str) and name_val:
                record_port_names.add(name_val)
            elif p.get("number") is not None:
                record_port_names.add(f"{anchor_guid}_{p['number']}")

    name_port_names: set[str] = {
        str(p.get("name", ""))
        for p in ports_by_name
        if isinstance(p, dict) and str(p.get("name", "")).strip()
    }
    guid_port_names: set[str] = {
        str(p.get("name", ""))
        for p in ports_by_guid
        if isinstance(p, dict) and str(p.get("name", "")).strip()
    }

    ghost_ports = sorted(record_port_names - name_port_names)
    name_only_ports = sorted(name_port_names - record_port_names)

    # Inferred diagnosis — order matters: check in exact spec order.
    if (
        not ghost_ports
        and len(name_port_names) == len(record_port_names) == len(guid_port_names)
        and name_port_names == guid_port_names == record_port_names
        and len(record_port_names) > 0
    ):
        diagnosis = "clean"
    elif len(record_port_names) == 0 and len(name_port_names) == 0 and len(guid_port_names) == 0:
        diagnosis = "empty_system"
    elif len(name_port_names) == 0 and len(guid_port_names) > 0:
        diagnosis = "host_node_desc_missing"
    elif len(guid_port_names) < len(name_port_names) and len(name_port_names) > 0:
        diagnosis = "stale_anchor"
    elif len(ghost_ports) > 0:
        diagnosis = "ghost_ports"
    elif (
        len(record_port_names) < len(name_port_names)
        and len(name_port_names) == len(guid_port_names)
        and not ghost_ports
    ):
        diagnosis = "record_undercount"
    else:
        diagnosis = "unknown"

    hints: dict[str, str] = {
        "stale_anchor": (
            "On the UFM HA primary: `sudo pcs resource restart ufm-enterprise`. "
            "Restarts UFM model layer (~1-2 min downtime), zero fabric impact, "
            "rebuilds inventory cache. See skills/ufm-stale-inventory-recovery/SKILL.md."
        ),
        "ghost_ports": (
            "Some ports listed in the system record are no longer present on the host. "
            "Likely stale entries from a previous configuration. "
            "`sudo pcs resource restart ufm-enterprise` clears the inventory cache."
        ),
        "host_node_desc_missing": (
            "UFM has the anchor GUID but no live ports under the system name — "
            "host's IB stack stopped advertising node_description. Check `ibstat` "
            "and node_desc on the host."
        ),
        "record_undercount": (
            "UFM's system record is missing ports that the host has live "
            "(both ?system=<guid> and the live view agree on more ports than the record). "
            "`sudo pcs resource restart ufm-enterprise` on the UFM HA primary "
            "rebuilds the inventory cache. See skills/ufm-stale-inventory-recovery/SKILL.md."
        ),
        "empty_system": (
            "System record exists but has no ports anywhere on the fabric. "
            "Likely a phantom entry from a removed host that wasn't cleaned up. "
            "UFM does not expose a DELETE on /resources/systems (returns 405); "
            "contact the fabric team or wait for a discovery sweep to remove it."
        ),
        "clean": "No drift detected.",
        "unknown": "Unrecognized drift pattern; capture this output and ping fabric-team.",
    }

    return _serializable_dict(
        {
            "ok": True,
            "system": {
                "name": sys_name,
                "anchor_guid": anchor_guid,
                "anchor_record_port_count": len(record_port_names),
            },
            "counts": {
                "record_ports": len(record_port_names),
                "ports_by_name": len(name_port_names),
                "ports_by_guid": len(guid_port_names),
            },
            "ghost_ports": ghost_ports,
            "name_only_ports": name_only_ports,
            "inferred_diagnosis": diagnosis,
            "remediation_hint": hints[diagnosis],
        }
    )


def _resolve_peer_summaries(
    client: Any, resources_base: str, selected_ports: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    peer_targets: set[tuple[str, str]] = set()
    for p in selected_ports:
        peer_guid = str(p.get("peer_guid") or p.get("peer_node_guid") or "").strip()
        peer_dname = str(p.get("peer_port_dname") or "").strip()
        if peer_guid and peer_dname:
            peer_targets.add((peer_guid, peer_dname))

    cache: dict[str, list[dict[str, Any]]] = {}
    for peer_guid, _ in peer_targets:
        if peer_guid not in cache:
            payload = client.get_json(
                f"{resources_base}/resources/ports", params={"system": peer_guid}
            )
            cache[peer_guid] = normalize_list_payload(payload)

    summaries: dict[str, dict[str, Any]] = {}
    for peer_guid, peer_dname in peer_targets:
        peer_ports = cache.get(peer_guid, [])
        match = next(
            (pp for pp in peer_ports if str(pp.get("dname", "")).strip() == peer_dname), None
        )
        if match is None:
            try:
                peer_num = int(peer_dname)
                match = next(
                    (pp for pp in peer_ports if int(pp.get("number", -1)) == peer_num), None
                )
            except (TypeError, ValueError):
                pass
        if match is not None:
            summaries[f"{peer_guid}_{peer_dname}"] = {
                "system_name": match.get("system_name"),
                "name": match.get("name"),
                "number": match.get("number"),
                "dname": match.get("dname"),
                "physical_state": match.get("physical_state"),
                "logical_state": match.get("logical_state"),
                "severity": match.get("severity"),
                "high_ber_severity": match.get("high_ber_severity"),
                "fec_mode": match.get("fec_mode"),
                "effective_ber": match.get("effective_ber"),
                "fec_uncorrectable": match.get("port_fec_uncorrectable_block_counter"),
                "fec_correctable": match.get("port_fec_correctable_block_counter"),
                "symbol_error_counter": match.get("symbol_error_counter"),
                "link_down_counter": match.get("link_down_counter"),
                "path": match.get("path"),
            }
    return summaries


def _collect_port_alarms(
    client: Any, api_base: str, selected_ports: list[dict[str, Any]], include_peer: bool
) -> dict[str, list[dict[str, Any]]]:
    alarms = client.get_json(f"{api_base}/app/alarms")
    if not isinstance(alarms, list):
        return {}
    wanted: set[str] = set()
    for p in selected_ports:
        if isinstance(p.get("name"), str):
            wanted.add(p["name"])
        if include_peer and isinstance(p.get("peer"), str):
            wanted.add(p["peer"])

    result: dict[str, list[dict[str, Any]]] = {}
    for a in alarms:
        if not isinstance(a, dict):
            continue
        obj = a.get("object_name")
        if obj in wanted:
            result.setdefault(str(obj), []).append(
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "description": a.get("description"),
                    "severity": a.get("severity"),
                    "timestamp": a.get("timestamp"),
                    "type": a.get("type"),
                    "object_name": obj,
                }
            )
    return result


# ================================================================
#  TOOLS: Port health + recent logs
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_check_ports_recent(
    system: Annotated[
        str,
        Field(
            default="",
            description=("System name or GUID. Omit when using port_guid or node_guid."),
        ),
    ] = "",
    port_numbers: Annotated[
        list[int] | None,
        Field(default=None, description="Port numbers to inspect. Omit to list all ports."),
    ] = None,
    lookback_minutes: Annotated[int, Field(default=15, ge=1, le=1440)] = 15,
    include_peer_ports: Annotated[bool, Field(default=True)] = True,
    include_alarms: Annotated[bool, Field(default=True)] = True,
    include_events: Annotated[
        bool, Field(default=True, description="Include recent Events for this system")
    ] = True,
    log_types: Annotated[
        list[LogType] | None, Field(default=None, description="Log types (default: ['UFM','SM'])")
    ] = None,
    log_length: Annotated[
        int, Field(default=10000, ge=1, le=10000, description="Log lines per type (max 10000)")
    ] = 10000,
    max_log_matches: Annotated[int, Field(default=200, ge=1, le=2000)] = 200,
    include_error_tail: Annotated[
        bool, Field(default=True, description="Include recent error-ish lines")
    ] = True,
    errors_only: Annotated[
        bool, Field(default=False, description="Only return ports with non-Info severity or alarms")
    ] = False,
    down_only: Annotated[
        bool,
        Field(default=False, description="Only return ports whose physical_state is not Active"),
    ] = False,
    port_guid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Port GUID (e.g. 0xa088c20300556b96 from `ibstat`). "
                "Bypasses system resolution. Provide exactly one of: system, port_guid, node_guid."
            ),
        ),
    ] = None,
    node_guid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "HCA/system node GUID; bypasses /resources/systems lookup entirely. "
                "Provide exactly one of: system, port_guid, node_guid."
            ),
        ),
    ] = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Check port health and correlate with recent logs/events in one call.

    Combines ufm_get_ports_health with UFM/SM log searches and recent events
    filtered to the target system. SM = Subnet Manager, the InfiniBand
    component that manages fabric routing and topology.

    Provide exactly one selector: system, port_guid, or node_guid.
    In sidedoor mode (port_guid or node_guid), log fetching is skipped
    because no system name is available for log token matching up front.
    """
    cfg = sites.get_config(site)
    client = sites.get_client(site)

    if log_types is None:
        log_types = ["UFM", "SM"]

    health = ufm_get_ports_health(
        system=system,
        port_numbers=port_numbers,
        include_peer_ports=include_peer_ports,
        include_alarms=include_alarms,
        include_cable_info=False,
        errors_only=errors_only,
        down_only=down_only,
        port_guid=port_guid,
        node_guid=node_guid,
        site=site,
    )
    if not isinstance(health, dict) or not health.get("ok"):
        return _serializable_dict(
            {"ok": False, "error": "Failed to resolve port health", "health": health}
        )

    # In sidedoor mode (port_guid / node_guid), skip log fetching — we have no
    # system name anchor for log token matching, and the caller just wants port
    # health directly.
    if port_guid or node_guid:
        # Sidedoor mode emits `inventory_source` at the top level for caller convenience.
        # The system-name path does NOT (it only sets `health.inventory_warnings` when
        # the stale-anchor fallback fires). This asymmetry is intentional: sidedoor
        # callers explicitly chose the bypass, so surfacing the bypass mode at the
        # top level mirrors the explicitness.
        return _serializable_dict(
            {
                "ok": True,
                "lookback_minutes": lookback_minutes,
                "server_tz": None,
                "health": health,
                "logs": {},
                "events": [],
                "inventory_source": health.get("inventory_source"),
                "note": "Sidedoor mode (port_guid/node_guid): log/event fetching skipped.",
            }
        )

    tokens: set[str] = set()
    sys_name = str((health.get("system") or {}).get("system_name") or "").strip()
    if sys_name:
        tokens.add(sys_name)
    for p in health.get("ports") or []:
        if not isinstance(p, dict):
            continue
        for k in ["name", "peer", "system_name", "peer_node_name", "path"]:
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                tokens.add(v.strip())
        peer_port = p.get("peer_port")
        if isinstance(peer_port, dict):
            for k in ["system_name", "name", "path"]:
                v = peer_port.get(k)
                if isinstance(v, str) and v.strip():
                    tokens.add(v.strip())

    tz_name, tzinfo = get_server_tzinfo(client, cfg.ufm_api_base_path)
    now_local = datetime.now(tzinfo) if tzinfo is not None else datetime.now()
    cutoff_local = now_local - timedelta(minutes=lookback_minutes)
    logs_base = cfg.ufm_logs_base_path

    def filter_log(log_type: str, content: str) -> dict[str, Any]:
        lines = content.splitlines()
        recent_lines = 0
        token_hits: list[str] = []
        error_lines: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            dt = (
                parse_ufm_log_ts(line, tzinfo)
                if log_type == "UFM"
                else parse_sm_log_ts(line, tzinfo, now_local.year)
            )
            if dt is None or dt < cutoff_local:
                continue
            recent_lines += 1
            if is_error_line(line):
                error_lines.append(line)
            if any(t.lower() in line.lower() for t in tokens if t):
                token_hits.append(line)
                if len(token_hits) >= max_log_matches:
                    break
        return {
            "window_start": cutoff_local.isoformat(),
            "window_end": now_local.isoformat(),
            "recent_lines_count": recent_lines,
            "token_match_count": len(token_hits),
            "token_matches": token_hits,
            "error_lines_count": len(error_lines),
            "error_lines_tail": error_lines[-30:] if include_error_tail else [],
        }

    logs_out: dict[str, Any] = {}
    for lt in log_types:
        resp = client.get_json(f"{logs_base}/app/logs/{lt}", params={"length": log_length})
        content = resp.get("content", "") if isinstance(resp, dict) else ""
        logs_out[lt] = {
            "ok": True,
            "fetched_lines": len(content.splitlines()) if content else 0,
            **filter_log(lt, content),
        }

    events_out: list[dict[str, Any]] = []
    if include_events:
        cutoff_utc = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        events = client.get_json(f"{cfg.ufm_api_base_path}/app/events")
        if isinstance(events, list):
            for e in events:
                if not isinstance(e, dict):
                    continue
                dt = parse_ts_utc(str(e.get("timestamp") or ""))
                if dt is None or dt < cutoff_utc:
                    continue
                obj = str(e.get("object_name") or "")
                path = str(e.get("object_path") or "")
                if sys_name and sys_name not in path and obj not in tokens:
                    continue
                events_out.append(summarize_event(e))

    return _serializable_dict(
        {
            "ok": True,
            "lookback_minutes": lookback_minutes,
            "server_tz": tz_name,
            "health": health,
            "logs": logs_out,
            "events": events_out,
            "note": "Logs filtered by server-local timestamps and token matches. If noisy, 10000 lines may not cover full lookback.",
        }
    )


# ================================================================
#  TOOLS: Links
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_check_links_recent(
    lookback_minutes: Annotated[int, Field(default=15, ge=1, le=1440)] = 15,
    include_links_summary: Annotated[bool, Field(default=True)] = True,
    include_non_info_links: Annotated[
        bool, Field(default=True, description="Include links with severity != Info")
    ] = True,
    max_non_info_links: Annotated[int, Field(default=50, ge=1, le=500)] = 50,
    include_events: Annotated[
        bool, Field(default=True, description="Include recent link-related events")
    ] = True,
    max_events: Annotated[int, Field(default=50, ge=1, le=500)] = 50,
    include_alarms: Annotated[
        bool, Field(default=True, description="Include recent link-related alarms")
    ] = True,
    max_alarms: Annotated[int, Field(default=50, ge=1, le=500)] = 50,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Summarize InfiniBand link health and recent link-related alarms/events.

    An IB 'link' connects two ports (e.g. switch-to-switch or switch-to-HCA).
    Links with non-Info severity indicate degraded connections. This tool
    aggregates link severity counts and correlates with recent alarms/events.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path
    api_base = cfg.ufm_api_base_path

    out: dict[str, Any] = {"ok": True, "lookback_minutes": lookback_minutes}

    if include_links_summary:
        links = normalize_list_payload(client.get_json(f"{resources_base}/resources/links"))
        if not links:
            out["links"] = {
                "total_links": 0,
                "severity_counts": {},
                "non_info_count": 0,
                "non_info_links": [],
            }
        else:
            sev_counts = count_severities(links)
            non_info_raw = [
                lk for lk in links if str(lk.get("severity", "")).strip().lower() != "info"
            ]
            non_info_out = []
            if include_non_info_links:
                for lk in non_info_raw[:max_non_info_links]:
                    non_info_out.append(
                        {
                            "severity": lk.get("severity"),
                            "name": lk.get("name"),
                            "source_guid": lk.get("source_guid"),
                            "source_port": lk.get("source_port"),
                            "source_port_dname": lk.get("source_port_dname"),
                            "source_port_node_description": lk.get("source_port_node_description"),
                            "destination_guid": lk.get("destination_guid"),
                            "destination_port": lk.get("destination_port"),
                            "destination_port_dname": lk.get("destination_port_dname"),
                            "destination_port_node_description": lk.get(
                                "destination_port_node_description"
                            ),
                            "width": lk.get("width"),
                        }
                    )
            out["links"] = {
                "total_links": len(links),
                "severity_counts": sev_counts,
                "non_info_count": len(non_info_raw),
                "non_info_links": non_info_out,
            }

    cutoff_utc = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
    out["now_utc"] = datetime.now(UTC).isoformat()
    out["cutoff_utc"] = cutoff_utc.isoformat()

    if include_events:
        events_payload = client.get_json(f"{api_base}/app/events")
        recent_events: list[dict[str, Any]] = []
        if isinstance(events_payload, list):
            for e in events_payload:
                if not isinstance(e, dict):
                    continue
                dt = parse_ts_utc(str(e.get("timestamp") or ""))
                if dt is None or dt < cutoff_utc:
                    continue
                if not is_linkish(e):
                    continue
                recent_events.append(summarize_event(e))
        sev = Counter(str(e.get("severity") or "Unknown") for e in recent_events)
        out["recent_link_events"] = {
            "count": len(recent_events),
            "severity_counts": dict(sev),
            "events": recent_events[:max_events],
        }

    if include_alarms:
        alarms_payload = client.get_json(f"{api_base}/app/alarms")
        recent_alarms: list[dict[str, Any]] = []
        if isinstance(alarms_payload, list):
            for a in alarms_payload:
                if not isinstance(a, dict):
                    continue
                dt = parse_ts_utc(str(a.get("timestamp") or ""))
                if dt is not None and dt < cutoff_utc:
                    continue
                if not is_linkish(a):
                    continue
                recent_alarms.append(summarize_alarm(a))
        sev = Counter(str(a.get("severity") or "Unknown") for a in recent_alarms)
        out["recent_link_alarms"] = {
            "count": len(recent_alarms),
            "severity_counts": dict(sev),
            "alarms": recent_alarms[:max_alarms],
        }

    return _serializable_dict(out)


# ================================================================
#  TOOLS: Logs
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_log(
    log_type: Annotated[LogType, Field(description="Log type: Event, SM, or UFM")] = "UFM",
    length: Annotated[
        int, Field(default=500, ge=1, le=10000, description="Max lines (UFM max: 10000)")
    ] = 500,
    limit_chars: Annotated[
        int, Field(default=20000, ge=1000, le=500000, description="Max chars to return")
    ] = 20000,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Download UFM log text. Log types: UFM (main server log), SM (Subnet Manager), Event (event log)."""
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_logs_base_path
    resp = client.get_json(f"{base}/app/logs/{log_type}", params={"length": length})

    if isinstance(resp, dict) and isinstance(resp.get("content"), str):
        content = resp["content"]
    else:
        return _serializable_dict(
            {"ok": True, "log_type": log_type, "length": length, "response": resp}
        )

    truncated_content, truncated = truncate_text(content, limit_chars)
    return {
        "ok": True,
        "log_type": log_type,
        "length": length,
        "truncated": truncated,
        "limit_chars": limit_chars,
        "content": truncated_content,
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_search_log(
    query: Annotated[str, Field(description="Case-insensitive substring to search for")],
    log_type: Annotated[LogType, Field(description="Log type: Event, SM, or UFM")] = "UFM",
    length: Annotated[
        int, Field(default=5000, ge=1, le=10000, description="Lines to fetch before searching")
    ] = 5000,
    max_matches: Annotated[int, Field(default=50, ge=1, le=500)] = 50,
    context_lines: Annotated[int, Field(default=2, ge=0, le=20)] = 2,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Search within a downloaded log (read-only). Quick triage, not full-text indexing."""
    if not query.strip():
        raise ToolError("query must be non-empty")

    got = ufm_get_log(log_type=log_type, length=length, limit_chars=500000, site=site)
    if not got.get("ok"):
        return got
    text = str(got.get("content", ""))

    q = query.lower()
    lines = text.splitlines()
    hits: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if q in line.lower():
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            hits.append({"line_number": i + 1, "line": line, "context": lines[start:end]})
            if len(hits) >= max_matches:
                break

    return {
        "ok": True,
        "log_type": log_type,
        "query": query,
        "matches": hits,
        "match_count": len(hits),
        "note": "Matches are from the fetched window only (controlled by length).",
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_search_logs(
    query: Annotated[str, Field(description="Substring or regex pattern to search for")],
    log_types: Annotated[list[LogType] | None, Field(default=None)] = None,
    length: Annotated[
        int, Field(default=10000, ge=1, le=10000, description="Lines per log type")
    ] = 10000,
    max_matches: Annotated[
        int, Field(default=100, ge=1, le=2000, description="Max total matches")
    ] = 100,
    context_lines: Annotated[int, Field(default=2, ge=0, le=50)] = 2,
    regex: Annotated[bool, Field(default=False, description="Treat query as regex")] = False,
    case_sensitive: Annotated[bool, Field(default=False)] = False,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Search across multiple UFM log types (read-only)."""
    if not query.strip():
        raise ToolError("query must be non-empty")
    if log_types is None:
        log_types = ["UFM", "SM"]
    if not log_types:
        raise ToolError("log_types must be non-empty")

    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_logs_base_path

    pattern = None
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags=flags)
        except re.error as e:
            raise ToolError(f"Invalid regex: {e}") from e
    else:
        q = query if case_sensitive else query.lower()

    hits: list[dict[str, Any]] = []
    counts: dict[str, int] = dict.fromkeys(log_types, 0)
    truncated = False

    for log_type in log_types:
        resp = client.get_json(f"{base}/app/logs/{log_type}", params={"length": length})
        if not (isinstance(resp, dict) and isinstance(resp.get("content"), str)):
            continue
        lines = resp["content"].splitlines()
        for i, line in enumerate(lines):
            hay = line if case_sensitive else line.lower()
            matched = bool(pattern.search(line)) if pattern else (q in hay)
            if not matched:
                continue
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            hits.append(
                {
                    "log_type": log_type,
                    "line_number": i + 1,
                    "line": line,
                    "context": lines[start:end],
                }
            )
            counts[log_type] = counts.get(log_type, 0) + 1
            if len(hits) >= max_matches:
                truncated = True
                break
        if truncated:
            break

    return {
        "ok": True,
        "query": query,
        "regex": regex,
        "case_sensitive": case_sensitive,
        "log_types": log_types,
        "length": length,
        "max_matches": max_matches,
        "context_lines": context_lines,
        "match_count": len(hits),
        "truncated": truncated,
        "counts_by_log_type": counts,
        "matches": hits,
        "note": "Matches are from the fetched window only (controlled by length).",
    }


# ================================================================
#  TOOLS: Log history + system dump (write operations)
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_create_log_history(
    log_type: Annotated[LogType, Field(description="Log type: Event, SM, or UFM")] = "Event",
    start_ms: Annotated[
        int | None, Field(default=None, description="Start time (ms epoch). Default: now-1h")
    ] = None,
    end_ms: Annotated[
        int | None, Field(default=None, description="End time (ms epoch). Default: now")
    ] = None,
    length: Annotated[int, Field(default=10000, ge=1, le=100000)] = 10000,
    tz: Annotated[str, Field(default="utc", description="Timezone name")] = "utc",
    event_src: Annotated[
        Literal["device", "link"] | None, Field(default=None, description="Only for log_type=Event")
    ] = None,
    allow_write: Annotated[
        bool, Field(default=False, description="Must be true to proceed")
    ] = False,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Create a server-side log history file (POST; requires allow_write=true)."""
    if not allow_write:
        raise ToolError("Refusing to create history without allow_write=true")
    client = sites.get_client(site)
    cfg = sites.get_config(site)

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        start_ms = end_ms - 60 * 60 * 1000

    base = cfg.ufm_logs_base_path
    params: dict[str, Any] = {"start": start_ms, "end": end_ms, "length": length, "tz": tz}
    if event_src is not None:
        if log_type != "Event":
            raise ToolError("event_src is only valid when log_type == 'Event'")
        params["event_src"] = event_src

    resp = client.post_no_body(f"{base}/app/logs/{log_type}/history", params=params)
    location = resp.headers.get("Location") or resp.headers.get("location")
    return {
        "ok": True,
        "status_code": resp.status_code,
        "location": location,
        "note": "Poll the referenced job; when complete, download via ufm_download_log_history_file(file_name=...).",
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
async def ufm_download_log_history_file(
    file_name: Annotated[str, Field(description="File name from the completed job summary")],
    limit_chars: Annotated[int, Field(default=200000, ge=1000, le=2000000)] = 200000,
    site: SiteParam = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Download a history file created by ufm_create_log_history."""
    if not file_name.strip():
        raise ToolError("file_name must be non-empty")

    if ctx:
        await ctx.report_progress(progress=0, total=2, message=f"Downloading {file_name}")

    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_web_base_path
    text = client.get_text(f"{base}/{file_name.lstrip('/')}", accept="text/plain")

    if ctx:
        await ctx.report_progress(progress=1, total=2, message="Truncating to limit")

    out, truncated = truncate_text(text, limit_chars)

    if ctx:
        await ctx.report_progress(progress=2, total=2, message="Complete")

    return {
        "ok": True,
        "file_name": file_name,
        "truncated": truncated,
        "limit_chars": limit_chars,
        "content": out,
    }


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_create_system_dump(
    mode: Annotated[
        SystemDumpMode, Field(description="Default (basic) or SnapShot (extended, includes logs)")
    ] = "SnapShot",
    allow_write: Annotated[
        bool, Field(default=False, description="Must be true to proceed")
    ] = False,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Trigger UFM system dump generation (POST; requires allow_write=true)."""
    if not allow_write:
        raise ToolError("Refusing to create system dump without allow_write=true")
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_backup_base_path
    resp = client.post_no_body(f"{base}/app/backup", params={"mode": mode})
    location = resp.headers.get("Location") or resp.headers.get("location")
    job_id = _parse_job_id_from_location(location)
    return {
        "ok": True,
        "mode": mode,
        "status_code": resp.status_code,
        "location": location,
        "job_id": job_id,
        "note": "Use ufm_get_job(job_id=...) to poll; job summary will indicate where the dump was saved.",
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_job(
    job_id: Annotated[int | None, Field(default=None, ge=1)] = None,
    job_path: Annotated[
        str | None, Field(default=None, description="Path from Location header")
    ] = None,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Fetch job status/details for a UFM job (read-only)."""
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    if job_path:
        path = job_path
    elif job_id is not None:
        path = f"{cfg.ufm_jobs_base_path}/jobs/{job_id}"
    else:
        raise ToolError("Either job_id or job_path is required")
    return _serializable_dict(client.get_json(path))


def _parse_job_id_from_location(location: str | None) -> int | None:
    if not location:
        return None
    m = re.search(r"/jobs/(\d+)", location)
    return int(m.group(1)) if m else None


async def _poll_job(
    client: Any,
    cfg: Any,
    job_id: int,
    *,
    timeout_seconds: int,
    poll_interval: int,
    ctx: Context | None = None,
    progress_step: int = 1,
    progress_total: int = 3,
) -> dict[str, Any]:
    """Poll a UFM job until completion or timeout."""
    elapsed = 0
    while elapsed < timeout_seconds:
        job_result = _serializable_dict(client.get_json(f"{cfg.ufm_jobs_base_path}/jobs/{job_id}"))
        status = str(job_result.get("Status") or job_result.get("status") or "unknown")
        if status.lower() in ("completed", "completed with errors"):
            return job_result
        if status.lower() in ("failed", "cancelled", "canceled"):
            raise ToolError(f"Job {job_id} ended with status: {status}")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if ctx:
            await ctx.report_progress(
                progress=progress_step,
                total=progress_total,
                message=f"Job {job_id}: {status} ({elapsed}s/{timeout_seconds}s)",
            )
    raise ToolError(f"Job {job_id} timed out after {timeout_seconds}s (last status: {status})")


# ================================================================
#  TOOLS: Combined create-and-wait operations
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
async def ufm_create_and_wait_log_history(
    log_type: Annotated[LogType, Field(description="Log type: Event, SM, or UFM")] = "Event",
    start_ms: Annotated[
        int | None, Field(default=None, description="Start time (ms epoch). Default: now-1h")
    ] = None,
    end_ms: Annotated[
        int | None, Field(default=None, description="End time (ms epoch). Default: now")
    ] = None,
    length: Annotated[int, Field(default=10000, ge=1, le=100000)] = 10000,
    tz: Annotated[str, Field(default="utc", description="Timezone name")] = "utc",
    event_src: Annotated[
        Literal["device", "link"] | None, Field(default=None, description="Only for log_type=Event")
    ] = None,
    timeout_seconds: Annotated[
        int, Field(default=300, ge=10, le=600, description="Max seconds to wait for job")
    ] = 300,
    poll_interval: Annotated[
        int, Field(default=5, ge=1, le=60, description="Seconds between job status checks")
    ] = 5,
    limit_chars: Annotated[int, Field(default=200000, ge=1000, le=2000000)] = 200000,
    allow_write: Annotated[
        bool, Field(default=False, description="Must be true to proceed")
    ] = False,
    site: SiteParam = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create a log history file, wait for the job to complete, and return the content.

    Combines ufm_create_log_history + job polling + ufm_download_log_history_file
    into a single operation with progress reporting.
    """
    if not allow_write:
        raise ToolError("Refusing to create history without allow_write=true")

    client = sites.get_client(site)
    cfg = sites.get_config(site)

    if ctx:
        await ctx.report_progress(progress=0, total=3, message="Creating log history job")

    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms
    if start_ms is None:
        start_ms = end_ms - 60 * 60 * 1000

    base = cfg.ufm_logs_base_path
    params: dict[str, Any] = {"start": start_ms, "end": end_ms, "length": length, "tz": tz}
    if event_src is not None:
        if log_type != "Event":
            raise ToolError("event_src is only valid when log_type == 'Event'")
        params["event_src"] = event_src

    resp = client.post_no_body(f"{base}/app/logs/{log_type}/history", params=params)
    location = resp.headers.get("Location") or resp.headers.get("location")
    job_id = _parse_job_id_from_location(location)
    if job_id is None:
        raise ToolError(f"Could not parse job ID from Location header: {location}")

    if ctx:
        await ctx.report_progress(progress=1, total=3, message=f"Waiting for job {job_id}")

    job_result = await _poll_job(
        client,
        cfg,
        job_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        ctx=ctx,
        progress_step=1,
        progress_total=3,
    )

    if ctx:
        await ctx.report_progress(progress=2, total=3, message="Downloading result")

    summary = job_result.get("Summary") or job_result.get("summary") or ""
    file_name = _extract_file_name_from_summary(str(summary))

    if not file_name:
        if ctx:
            await ctx.report_progress(progress=3, total=3, message="Complete (no file to download)")
        return {
            "ok": True,
            "job_id": job_id,
            "job": job_result,
            "note": "Job completed but no downloadable file was found in the summary.",
        }

    download_result = await ufm_download_log_history_file(
        file_name=file_name,
        limit_chars=limit_chars,
        site=site,
    )

    if ctx:
        await ctx.report_progress(progress=3, total=3, message="Complete")

    return {
        "ok": True,
        "job_id": job_id,
        "file_name": file_name,
        "job": job_result,
        **{k: v for k, v in download_result.items() if k != "ok"},
    }


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": False},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
async def ufm_create_and_wait_system_dump(
    mode: Annotated[
        SystemDumpMode, Field(description="Default (basic) or SnapShot (extended, includes logs)")
    ] = "SnapShot",
    timeout_seconds: Annotated[
        int, Field(default=300, ge=10, le=600, description="Max seconds to wait for job")
    ] = 300,
    poll_interval: Annotated[
        int, Field(default=5, ge=1, le=60, description="Seconds between job status checks")
    ] = 5,
    allow_write: Annotated[
        bool, Field(default=False, description="Must be true to proceed")
    ] = False,
    site: SiteParam = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create a system dump, wait for the job to complete, and return the result location.

    Combines ufm_create_system_dump + job polling into a single operation
    with progress reporting.
    """
    if not allow_write:
        raise ToolError("Refusing to create system dump without allow_write=true")

    client = sites.get_client(site)
    cfg = sites.get_config(site)

    if ctx:
        await ctx.report_progress(progress=0, total=2, message=f"Creating {mode} system dump")

    base = cfg.ufm_backup_base_path
    resp = client.post_no_body(f"{base}/app/backup", params={"mode": mode})
    location = resp.headers.get("Location") or resp.headers.get("location")
    job_id = _parse_job_id_from_location(location)
    if job_id is None:
        raise ToolError(f"Could not parse job ID from Location header: {location}")

    if ctx:
        await ctx.report_progress(progress=1, total=2, message=f"Waiting for job {job_id}")

    job_result = await _poll_job(
        client,
        cfg,
        job_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        ctx=ctx,
        progress_step=1,
        progress_total=2,
    )

    if ctx:
        await ctx.report_progress(progress=2, total=2, message="Complete")

    return {
        "ok": True,
        "mode": mode,
        "job_id": job_id,
        "job": job_result,
        "note": "System dump job completed. Check job summary for file location.",
    }


def _extract_file_name_from_summary(summary: str) -> str | None:
    """Try to pull a downloadable file path from a UFM job summary string."""
    m = re.search(r"(/[^\s]+\.(?:txt|csv|log|gz|tar\.gz|tgz|zip))", summary)
    if m:
        return m.group(1)
    m = re.search(r"([^\s/]+\.(?:txt|csv|log|gz|tar\.gz|tgz|zip))", summary)
    return m.group(1) if m else None


# ================================================================
#  TOOLS: Cluster triage (one-call aggregator)
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_cluster_concerns(
    lookback_minutes: Annotated[int, Field(default=30, ge=1, le=1440)] = 30,
    include_logs: Annotated[bool, Field(default=True)] = True,
    include_high_ber: Annotated[bool, Field(default=True)] = True,
    include_links: Annotated[bool, Field(default=True)] = True,
    log_length: Annotated[
        int, Field(default=10000, ge=1, le=10000, description="Log lines for UFM/SM logs")
    ] = 10000,
    max_items: Annotated[int, Field(default=10, ge=1, le=200)] = 10,
    site: SiteParam = None,
) -> dict[str, Any]:
    """One-call cluster triage: alarms, events, logs, high-BER ports, and links.

    START HERE for any InfiniBand fabric investigation. Returns a consolidated
    summary of all active concerns across the cluster within the lookback window.
    Drill into specific areas with ufm_check_high_ber_recent, ufm_check_ports_recent,
    or ufm_search_logs.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    api_base = cfg.ufm_api_base_path
    logs_base = cfg.ufm_logs_base_path

    now_utc = datetime.now(UTC)
    cutoff_utc = now_utc - timedelta(minutes=lookback_minutes)

    alarms_payload = client.get_json(f"{api_base}/app/alarms")
    events_payload = client.get_json(f"{api_base}/app/events")
    alarms = alarms_payload if isinstance(alarms_payload, list) else []
    events = events_payload if isinstance(events_payload, list) else []

    alarm_sev_counts: Counter[str] = Counter()
    alarm_name_counts: Counter[str] = Counter()
    non_info_alarms: list[dict[str, Any]] = []
    for a in alarms:
        if not isinstance(a, dict):
            continue
        sev = str(a.get("severity") or "Unknown")
        alarm_sev_counts[sev] += 1
        alarm_name_counts[str(a.get("name") or "Unknown")] += 1
        if sev.lower() != "info":
            non_info_alarms.append(a)
    non_info_alarms.sort(key=lambda a: str(a.get("timestamp") or ""), reverse=True)

    recent_non_info_events: list[dict[str, Any]] = []
    recent_event_sev_counts: Counter[str] = Counter()
    recent_event_name_counts: Counter[str] = Counter()
    for e in events:
        if not isinstance(e, dict):
            continue
        sev = str(e.get("severity") or "Unknown")
        if sev.lower() == "info":
            continue
        dt = parse_ts_utc(str(e.get("timestamp") or ""))
        if dt is None or dt < cutoff_utc:
            continue
        recent_event_sev_counts[sev] += 1
        recent_event_name_counts[str(e.get("name") or "Unknown")] += 1
        recent_non_info_events.append(e)
    recent_non_info_events.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)

    out: dict[str, Any] = {
        "ok": True,
        "now_utc": now_utc.isoformat(),
        "cutoff_utc": cutoff_utc.isoformat(),
        "lookback_minutes": lookback_minutes,
        "alarms": {
            "total": len([a for a in alarms if isinstance(a, dict)]),
            "severity_counts": dict(alarm_sev_counts),
            "top_alarm_names": top_n(dict(alarm_name_counts), max_items),
            "non_info_count": len(non_info_alarms),
            "non_info_sample": [summarize_alarm(a) for a in non_info_alarms[:max_items]],
        },
        "events": {
            "total": len([e for e in events if isinstance(e, dict)]),
            "recent_non_info_count": len(recent_non_info_events),
            "recent_severity_counts": dict(recent_event_sev_counts),
            "top_recent_event_names": top_n(dict(recent_event_name_counts), max_items),
            "recent_sample": [summarize_event(e) for e in recent_non_info_events[:max_items]],
            "note": "Events filtered by timestamp >= now-lookback (UTC; best-effort).",
        },
    }

    if include_logs:
        tz_name, tzinfo = get_server_tzinfo(client, cfg.ufm_api_base_path)
        now_local = datetime.now(tzinfo) if tzinfo is not None else datetime.now()
        cutoff_local = now_local - timedelta(minutes=lookback_minutes)

        def summarize_log(log_type: str, content: str) -> dict[str, Any]:
            recent_lines = err_lines = sm_mcmr_err = 0
            tail: list[str] = []
            for line in content.splitlines():
                if not line.strip():
                    continue
                dt = (
                    parse_ufm_log_ts(line, tzinfo)
                    if log_type == "UFM"
                    else parse_sm_log_ts(line, tzinfo, now_local.year)
                )
                if dt is None or dt < cutoff_local:
                    continue
                recent_lines += 1
                if is_error_line(line):
                    err_lines += 1
                    tail.append(line)
                    tail = tail[-max_items:]
                if log_type == "SM" and "mcmr_rcv_join_mgrp" in line and "ERR 1B11" in line:
                    sm_mcmr_err += 1
            deduped_tail = deduplicate_log_lines(tail)
            result = {
                "recent_lines_count": recent_lines,
                "recent_errorish_lines_count": err_lines,
                "recent_errorish_tail": deduped_tail,
            }
            if log_type == "SM":
                result["mcmr_rcv_join_mgrp_err_1b11_count"] = sm_mcmr_err
            return result

        ufm_resp = client.get_json(f"{logs_base}/app/logs/UFM", params={"length": log_length})
        sm_resp = client.get_json(f"{logs_base}/app/logs/SM", params={"length": log_length})
        ufm_text = ufm_resp.get("content", "") if isinstance(ufm_resp, dict) else ""
        sm_text = sm_resp.get("content", "") if isinstance(sm_resp, dict) else ""

        out["logs"] = {
            "server_tz": tz_name,
            "window_local_start": cutoff_local.isoformat(),
            "window_local_end": now_local.isoformat(),
            "UFM": summarize_log("UFM", ufm_text),
            "SM": summarize_log("SM", sm_text),
            "note": f"Logs parsed by server-local timestamps; {log_length} lines may not cover full lookback window.",
        }

    if include_high_ber:
        out["high_ber"] = ufm_check_high_ber_recent(
            lookback_minutes=lookback_minutes,
            max_ports=max_items,
            site=site,
        )

    if include_links:
        out["links"] = ufm_check_links_recent(
            lookback_minutes=lookback_minutes,
            include_links_summary=True,
            include_non_info_links=False,
            include_events=False,
            include_alarms=False,
            site=site,
        )

    return _serializable_dict(out)


# ================================================================
#  TOOLS: Partition key (pkey) management
# ================================================================


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_list_pkeys(site: SiteParam = None) -> dict[str, Any]:
    """List all InfiniBand partition keys (pkeys) configured in UFM.

    Partition keys segment the IB fabric into isolated communication domains,
    similar to VLANs. Each pkey has a hex identifier (e.g. 0x1, 0x7fff) and
    a membership list of port GUIDs.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    result = client.get_json(f"{base}/resources/pkeys")
    if isinstance(result, list):
        return {"ok": True, "count": len(result), "pkeys": ensure_json_serializable(result)}
    return _serializable_dict({"ok": True, "data": result})


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_pkey(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1', '0x7fff')")],
    guids_data: Annotated[
        bool, Field(default=True, description="Include GUID membership data")
    ] = True,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Get details for a specific partition key, optionally including GUID membership.

    When guids_data=true, returns the full list of GUIDs assigned to this pkey
    along with their membership type (full/limited) and host information.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    params: dict[str, Any] = {}
    if guids_data:
        params["guids_data"] = "true"
    result = client.get_json(f"{base}/resources/pkeys/{pkey}", params=params or None)
    return _serializable_dict({"ok": True, "pkey": pkey, "data": ensure_json_serializable(result)})


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_add_guids_to_pkey(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1')")],
    guids: Annotated[
        list[str],
        Field(min_length=1, description="Port GUIDs to add (e.g. ['0x0002c9030005f34a'])"),
    ],
    membership: Annotated[
        MembershipType, Field(default="full", description="Membership type: full or limited")
    ] = "full",
    ip_over_ib: Annotated[
        bool, Field(default=True, description="Enable IP over InfiniBand for these GUIDs")
    ] = True,
    index0: Annotated[
        bool,
        Field(default=False, description="Whether pkey is at index 0 of the port's pkey table"),
    ] = False,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Add port GUIDs to an InfiniBand partition key.

    GUIDs are the unique 64-bit identifiers of HCA/switch ports.
    Membership 'full' allows the port to send/receive, 'limited' is receive-only.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    body = {
        "pkey": pkey,
        "guids": guids,
        "membership": membership,
        "ip_over_ib": ip_over_ib,
        "index0": index0,
    }
    try:
        result = client.post_json(f"{base}/resources/pkeys", json_body=body)
    except httpx.HTTPStatusError as exc:
        detail: Any = exc.response.text
        try:
            detail = exc.response.json()
        except Exception:
            pass
        return _serializable_dict(
            {
                "ok": False,
                "error": f"UFM API error ({exc.response.status_code})",
                "detail": detail,
                "pkey": pkey,
                "guids": guids,
                "hint": "One or more GUIDs may not be known to UFM. Check UFM topology or wait for subnet manager discovery.",
            }
        )
    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "guids_added": len(guids),
            "response": ensure_json_serializable(result),
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_remove_guids_from_pkey(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1')")],
    guids: Annotated[list[str], Field(min_length=1, description="Port GUIDs to remove")],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Remove specific port GUIDs from an InfiniBand partition key.

    This removes only the listed GUIDs; other members of the pkey are unaffected.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    guids_csv = ",".join(g.strip() for g in guids)
    try:
        result = client.delete_json(f"{base}/resources/pkeys/{pkey}/guids/{guids_csv}")
    except httpx.HTTPStatusError as exc:
        detail: Any = exc.response.text
        try:
            detail = exc.response.json()
        except Exception:
            pass
        return _serializable_dict(
            {
                "ok": False,
                "error": f"UFM API error ({exc.response.status_code})",
                "detail": detail,
                "pkey": pkey,
                "guids": guids,
                "hint": "One or more GUIDs may not exist in this pkey or are not known to UFM.",
            }
        )
    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "guids_removed": len(guids),
            "response": ensure_json_serializable(result),
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_remove_hosts_from_pkey(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1')")],
    hosts: Annotated[
        list[str],
        Field(min_length=1, description="Hostnames to remove (all their GUIDs will be removed)"),
    ],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Remove all GUIDs belonging to specified hosts from a partition key.

    This is a convenience operation — rather than looking up individual GUIDs,
    you can remove an entire host by name. All ports for that host are removed
    from the pkey.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    hosts_csv = ",".join(h.strip() for h in hosts)
    try:
        result = client.delete_json(f"{base}/resources/pkeys/{pkey}/hosts/{hosts_csv}")
    except httpx.HTTPStatusError as exc:
        detail: Any = exc.response.text
        try:
            detail = exc.response.json()
        except Exception:
            pass
        return _serializable_dict(
            {
                "ok": False,
                "error": f"UFM API error ({exc.response.status_code})",
                "detail": detail,
                "pkey": pkey,
                "hosts": hosts,
                "hint": "One or more hosts may not exist in this pkey or are not known to UFM.",
            }
        )
    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "hosts_removed": len(hosts),
            "response": ensure_json_serializable(result),
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_add_hosts_to_pkey(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1')")],
    hosts: Annotated[
        list[str], Field(min_length=1, description="Hostnames to add (e.g. ['node01', 'node02'])")
    ],
    membership: Annotated[
        MembershipType, Field(default="full", description="Membership type: full or limited")
    ] = "full",
    ip_over_ib: Annotated[
        bool, Field(default=True, description="Enable IP over InfiniBand for these hosts")
    ] = True,
    index0: Annotated[
        bool,
        Field(default=False, description="Whether pkey is at index 0 of the port's pkey table"),
    ] = False,
    site: SiteParam = None,
) -> dict[str, Any]:
    """Add hosts to an InfiniBand partition key by hostname.

    UFM resolves hostnames to their HCA port GUIDs and adds all of them
    to the specified pkey. This is a convenience over ufm_add_guids_to_pkey
    when you know the hostnames but not the individual port GUIDs.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path
    hosts_names = ",".join(h.strip() for h in hosts)

    body: dict[str, Any] = {"pkey": pkey, "hosts_names": hosts_names}
    body.update(_pkey_body_extras(membership, ip_over_ib, index0))

    def _add_hosts_error_response(
        exc: httpx.HTTPStatusError,
        *,
        error_phase: Literal["first_request", "fallback_request", "guid_fallback"],
        hint: str,
    ) -> dict[str, Any]:
        detail_err = _http_response_error_detail(exc.response)
        return _serializable_dict(
            {
                "ok": False,
                "error": f"UFM API error ({exc.response.status_code})",
                "detail": detail_err,
                "pkey": pkey,
                "hosts": hosts,
                "hint": hint,
                "error_phase": error_phase,
            }
        )

    def _guid_fallback() -> dict[str, Any]:
        """Resolve hostnames to GUIDs via systems and retry with ufm_add_guids_to_pkey."""
        systems = client.get_json(f"{base}/resources/systems")
        if not isinstance(systems, list):
            systems = []
        guid_map = build_guid_to_hostname_map(systems)
        hostname_to_guids: dict[str, list[str]] = {}
        for guid, hostname in guid_map.items():
            hostname_to_guids.setdefault(hostname, []).append(guid)

        resolved_guids: list[str] = []
        unresolved_hosts: list[str] = []
        for h in hosts:
            h_stripped = h.strip()
            matched = hostname_to_guids.get(h_stripped, [])
            if matched:
                resolved_guids.extend(matched)
            else:
                unresolved_hosts.append(h_stripped)

        if not resolved_guids:
            return _serializable_dict(
                {
                    "ok": False,
                    "error": "GUID fallback: no GUIDs resolved from hostnames",
                    "pkey": pkey,
                    "hosts": hosts,
                    "unresolved_hosts": unresolved_hosts,
                    "error_phase": "guid_fallback",
                    "hint": _HINT_ADD_HOSTS_GUID_FALLBACK_FAILED,
                }
            )

        guid_body: dict[str, Any] = {"pkey": pkey, "guids": resolved_guids}
        guid_body.update(_pkey_body_extras(membership, ip_over_ib, index0))

        try:
            guid_result = client.post_json(f"{base}/resources/pkeys", json_body=guid_body)
        except httpx.HTTPStatusError as exc_guid:
            return _add_hosts_error_response(
                exc_guid,
                error_phase="guid_fallback",
                hint=_HINT_ADD_HOSTS_GUID_FALLBACK_FAILED,
            )

        resp: dict[str, Any] = {
            "ok": True,
            "pkey": pkey,
            "hosts_added": len(hosts) - len(unresolved_hosts),
            "guids_added": len(resolved_guids),
            "response": ensure_json_serializable(guid_result),
            "fallback_used": "guid",
            "note": (
                "UFM rejected the host-based request (schema/additionalProperties); "
                "succeeded by resolving hostnames to GUIDs."
            ),
        }
        if unresolved_hosts:
            resp["unresolved_hosts"] = unresolved_hosts
        return _serializable_dict(resp)

    try:
        result = client.post_json(f"{base}/resources/pkeys", json_body=body)
    except httpx.HTTPStatusError as exc_first:
        detail_first = _http_response_error_detail(exc_first.response)
        if not _ufm_detail_suggests_additional_properties_rejection(detail_first):
            return _add_hosts_error_response(
                exc_first,
                error_phase="first_request",
                hint=_HINT_ADD_HOSTS_FIRST_REQUEST,
            )
        return _guid_fallback()
    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "hosts_added": len(hosts),
            "response": ensure_json_serializable(result),
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_get_pkey_hosts(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1', '0x7fff')")],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Get partition key membership resolved to hostnames instead of raw GUIDs.

    Fetches pkey GUID membership and all systems, then maps each GUID to its
    hostname. Returns a host-level summary with hostname, GUID count, and
    membership type — much more useful for operators than raw GUID lists.
    """
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    base = cfg.ufm_resources_base_path

    pkey_result = client.get_json(f"{base}/resources/pkeys/{pkey}", params={"guids_data": "true"})
    systems = client.get_json(f"{base}/resources/systems")
    if not isinstance(systems, list):
        systems = []

    guid_map = build_guid_to_hostname_map(systems)
    host_summary, unresolved = resolve_pkey_guids_to_hosts(pkey_result, guid_map)

    total_guids = sum(h["guid_count"] for h in host_summary) + len(unresolved)
    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "total_guids": total_guids,
            "hosts_count": len(host_summary),
            "hosts": host_summary,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }
    )


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_pkey_diff(
    pkey: Annotated[str, Field(description="Partition key hex value (e.g. '0x1')")],
    expected_hosts: Annotated[
        list[str],
        Field(min_length=1, description="Expected hostnames that should be in this pkey"),
    ],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Compare current pkey host membership against an expected host list.

    Returns to_add (hosts missing from pkey), to_remove (hosts in pkey but not
    expected), and unchanged (hosts in both). Dry-run only — does not modify
    the pkey. Use ufm_add_hosts_to_pkey / ufm_remove_hosts_from_pkey to apply.
    """
    hosts_result = ufm_get_pkey_hosts(pkey=pkey, site=site)
    if not hosts_result.get("ok"):
        return hosts_result

    current_hosts = [h["hostname"] for h in hosts_result.get("hosts", [])]
    diff = pkey_diff(current_hosts, expected_hosts)

    return _serializable_dict(
        {
            "ok": True,
            "pkey": pkey,
            "to_add": diff["to_add"],
            "to_add_count": len(diff["to_add"]),
            "to_remove": diff["to_remove"],
            "to_remove_count": len(diff["to_remove"]),
            "unchanged": diff["unchanged"],
            "unchanged_count": len(diff["unchanged"]),
            "current_hosts_count": len(current_hosts),
            "expected_hosts_count": len(expected_hosts),
        }
    )


# ================================================================
#  TOOLS: Topaz fabric health
# ================================================================


def _get_topaz_settings() -> Settings:
    if _base_settings is None:
        from fastmcp.exceptions import ToolError

        raise ToolError("Settings not initialized. Configure UFM connection first.")
    return _base_settings


def _get_topaz_client():  # type: ignore[no-untyped-def]
    from ufm_mcp.topaz_client import TopazClient

    return TopazClient(_get_topaz_settings().topaz_endpoint)


def _resolve_topaz_az(site: str) -> str:
    az_map = _get_topaz_settings().topaz_az_map
    az_id = az_map.get(site) or az_map.get(site.lower())
    if not az_id:
        from fastmcp.exceptions import ToolError

        raise ToolError(f"Unknown Topaz site '{site}'. Known: {', '.join(sorted(az_map.keys()))}")
    return az_id


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_topaz_fabric_health(
    site: Annotated[str, Field(description="Site name (e.g. 'ori', '5c_oh1')")],
) -> dict[str, Any]:
    """Get Topaz fabric health summary for a site.

    Returns overall fabric health status, score, summary of nodes/switches/links,
    error counts by type, problematic ports, and issue summaries.
    """
    az_id = _resolve_topaz_az(site)
    client = _get_topaz_client()
    try:
        result = client.get_fabric_health(az_id)
    finally:
        client.close()
    if "problematicPorts" in result and len(result["problematicPorts"]) > 20:
        total = len(result["problematicPorts"])
        result["problematicPorts"] = result["problematicPorts"][:20]
        result["problematicPortsTruncated"] = True
        result["totalProblematicPorts"] = total
    result["site"] = site
    result["az_id"] = az_id
    if "ok" not in result:
        result["ok"] = True
    return _serializable_dict(result)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_topaz_port_counters(
    site: Annotated[str, Field(description="Site name (e.g. 'ori', '5c_oh1')")],
    errors_only: Annotated[
        bool, Field(default=False, description="Only return ports with errors")
    ] = False,
    guid_filter: Annotated[
        str | None, Field(default=None, description="Filter by switch/port GUID")
    ] = None,
) -> dict[str, Any]:
    """List Topaz port counters for a site.

    Returns per-port counter data including error counters, link state,
    FEC mode, BER metrics, and remote endpoint info.
    """
    az_id = _resolve_topaz_az(site)
    client = _get_topaz_client()
    try:
        result = client.list_port_counters(az_id, errors_only=errors_only, guid_filter=guid_filter)
    finally:
        client.close()
    result["site"] = site
    result["az_id"] = az_id
    if "ok" not in result:
        result["ok"] = True
    return _serializable_dict(result)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_topaz_cables(
    site: Annotated[str, Field(description="Site name (e.g. 'ori', '5c_oh1')")],
    alarms_only: Annotated[
        bool, Field(default=False, description="Only return cables with latched alarms")
    ] = False,
) -> dict[str, Any]:
    """List Topaz cable/transceiver info for a site.

    Returns cable vendor, part number, serial, temperature, optical power
    levels, bias current, and latched alarms.
    """
    az_id = _resolve_topaz_az(site)
    client = _get_topaz_client()
    try:
        result = client.list_cables(az_id, alarms_only=alarms_only)
    finally:
        client.close()
    result["site"] = site
    result["az_id"] = az_id
    if "ok" not in result:
        result["ok"] = True
    return _serializable_dict(result)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_topaz_switches(
    site: Annotated[str, Field(description="Site name (e.g. 'ori', '5c_oh1')")],
    errors_only: Annotated[
        bool, Field(default=False, description="Only return switches with errors")
    ] = False,
) -> dict[str, Any]:
    """List Topaz switches for a site.

    Returns switch summaries including GUID, description, model, firmware,
    port counts, and error status.
    """
    az_id = _resolve_topaz_az(site)
    client = _get_topaz_client()
    try:
        result = client.list_switches(az_id, errors_only=errors_only)
    finally:
        client.close()
    result["site"] = site
    result["az_id"] = az_id
    if "ok" not in result:
        result["ok"] = True
    return _serializable_dict(result)


@mcp.tool(
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_upload_ibdiagnet(
    site: Annotated[str, Field(description="Site name (e.g. 'ori', '5c_oh1')")],
    ibdiagnet_path: Annotated[
        str, Field(description="Path to the ibdiagnet tarball (.tar.gz) on the local filesystem")
    ],
    filename: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional filename hint for Topaz logging. Defaults to basename of ibdiagnet_path.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Upload an ibdiagnet tarball to Topaz for server-side parsing.

    Reads the local file at `ibdiagnet_path`, sends the bytes via the
    Topaz HealthService UploadIbdiagnet RPC, and returns the new
    collection_id so callers can pivot to topaz-cables /
    topaz-port-counters with --collection <id>.
    """
    import os

    from fastmcp.exceptions import ToolError

    if not os.path.exists(ibdiagnet_path):
        raise ToolError(f"File not found: {ibdiagnet_path}")
    if not os.path.isfile(ibdiagnet_path):
        raise ToolError(f"Not a file: {ibdiagnet_path}")

    with open(ibdiagnet_path, "rb") as f:
        tarball_data = f.read()

    if not tarball_data:
        raise ToolError(f"File is empty: {ibdiagnet_path}")

    az_id = _resolve_topaz_az(site)
    client = _get_topaz_client()
    try:
        result = client.upload_ibdiagnet(
            az_id=az_id,
            tarball_data=tarball_data,
            filename=filename or os.path.basename(ibdiagnet_path),
        )
    finally:
        client.close()

    result["site"] = site
    result["az_id"] = az_id
    result["uploaded_bytes"] = len(tarball_data)
    result["source_path"] = ibdiagnet_path
    if "ok" not in result:
        result["ok"] = "collection_id" in result or "collectionId" in result
    return _serializable_dict(result)


# ================================================================
#  MCP Prompts (for triage workflows)
# ================================================================


@mcp.prompt
def ufm_triage(site: str = "") -> str:
    """Quick triage workflow for a UFM-managed InfiniBand cluster."""
    site_note = f" on site '{site}'" if site else ""
    return (
        f"Please perform a quick triage of the UFM cluster{site_note}.\n\n"
        "1. Call ufm_get_cluster_concerns to get the high-level summary.\n"
        "2. If there are high-BER ports, investigate with ufm_check_high_ber_recent.\n"
        "3. For specific ports, use ufm_check_ports_recent with the system name and port numbers.\n"
        "4. Search logs with ufm_search_logs if you need more detail.\n"
        "5. Summarize findings with severity, affected systems, and recommended actions."
    )


@mcp.prompt
def ufm_investigate_port(system: str, port_numbers: str) -> str:
    """Investigate specific ports on a system."""
    return (
        f"Investigate ports {port_numbers} on system '{system}'.\n\n"
        "1. Call ufm_check_ports_recent with the system and port numbers.\n"
        "2. Review the port health, peer ports, alarms, and recent log matches.\n"
        "3. Check if peer ports also show issues.\n"
        "4. Summarize the state of each port and recommend next steps."
    )


@mcp.prompt
def ufm_log_search(query: str, log_types: str = "UFM,SM") -> str:
    """Search UFM logs for a specific pattern."""
    return (
        f"Search UFM logs for '{query}' across log types: {log_types}.\n\n"
        "1. Call ufm_search_logs with the query.\n"
        "2. Review the matches and their context.\n"
        "3. Identify patterns, frequencies, and affected systems.\n"
        "4. Summarize the findings."
    )


# ================================================================
#  Entry point
# ================================================================


_initialized = False


def _initialize(settings: Settings) -> None:
    """Initialize sites and logging from settings. Idempotent."""
    global _base_settings, _initialized
    if _initialized:
        return

    setup_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        name="ufm-mcp",
        system_log=True,
    )
    sites.configure(settings)
    _base_settings = settings
    _initialized = True


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage:
        uvicorn ufm_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
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


def main() -> None:
    global _base_settings
    suppress_ssl_warnings()

    cli_overlay = _parse_cli_args()
    try:
        base_settings = Settings(**cli_overlay)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    _initialize(base_settings)
    logger.info("Effective configuration: %s", sites.get_effective_summary())

    if not base_settings.verify_ssl:
        logger.warning("SSL certificate verification is DISABLED.")

    atexit.register(sites.close_all)

    if base_settings.transport == "http":
        if base_settings.host in ["0.0.0.0", "::", "[::]"]:
            logger.warning(
                "HTTP transport bound to %s:%s (all interfaces).",
                base_settings.host,
                base_settings.port,
            )
        if base_settings.mcp_http_access_token:
            mcp.add_middleware(
                HttpAccessTokenAuth(base_settings.mcp_http_access_token.get_secret_value().strip())
            )
        else:
            logger.warning("HTTP transport started without MCP_HTTP_ACCESS_TOKEN.")
        mcp.run(transport="http", host=base_settings.host, port=base_settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
