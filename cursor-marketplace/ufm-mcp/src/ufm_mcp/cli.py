"""
ufm-cli: Thin CLI wrapper around the UFM REST API.

Provides the same capabilities as ufm-mcp but via shell commands,
enabling AI agents to use UFM with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from mcp_common import setup_logging
from mcp_common.agent_remediation import install_cli_exception_handler

from ufm_mcp.config import Settings
from ufm_mcp.helpers import count_severities, ensure_json_serializable
from ufm_mcp.server import sites

app = typer.Typer(
    name="ufm-cli",
    help="Query NVIDIA UFM InfiniBand fabric data. Use --help on any subcommand for details.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/ufm-mcp")

_initialized = False
_cli_settings: Settings | None = None


def _load_dotenv() -> None:
    """Load .env files into os.environ so SiteManager discovery sees them.

    Pydantic-settings loads .env for model fields, but SiteManager reads
    os.environ directly for UFM_DEFAULT_SITE, UFM_<SITE>_URL, and
    UFM_SITE_ALIASES_JSON.  We need those in the real environment.
    """
    for candidate in [Path(".env"), Path("../.env")]:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            break


def _ensure_init() -> None:
    """Lazy-init the server's SiteManager from env vars / .env files."""
    global _initialized, _cli_settings
    if _initialized:
        return

    _load_dotenv()

    try:
        settings = Settings()
    except Exception as e:
        typer.echo(f"Error: Configuration failed: {e}", err=True)
        typer.echo(
            "Hint: set UFM_URL or create a .env file with UFM_URL=https://...",
            err=True,
        )
        raise typer.Exit(1) from e

    _cli_settings = settings
    sites.configure(settings)
    _initialized = True


def _output(data: Any, as_json: bool = False) -> None:
    """Print output — compact text by default, JSON with --json."""
    if as_json:
        typer.echo(json.dumps(ensure_json_serializable(data), indent=2, default=str))
        return

    if isinstance(data, dict):
        _print_dict(data, indent=0)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _print_dict(item, indent=0)
                typer.echo()
            else:
                typer.echo(str(item))
    else:
        typer.echo(str(data))


def _print_dict(d: dict, indent: int = 0) -> None:
    """Recursively print a dict in compact readable form."""
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            typer.echo(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            if not v:
                typer.echo(f"{prefix}{k}: []")
            elif len(v) <= 3 and all(not isinstance(x, (dict, list)) for x in v):
                typer.echo(f"{prefix}{k}: {v}")
            else:
                typer.echo(f"{prefix}{k}: [{len(v)} items]")
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        typer.echo(f"{prefix}  [{i}]")
                        _print_dict(item, indent + 2)
                    else:
                        typer.echo(f"{prefix}  {item}")
        else:
            typer.echo(f"{prefix}{k}: {v}")


def _format_alarm_line(a: dict) -> str:
    sev = a.get("severity", "?")
    name = a.get("name", "?")
    obj = a.get("resolved_name") or a.get("object_name", "")
    desc = a.get("description", "")
    ts = a.get("timestamp", "")
    parts = [f"  [{sev}] {name}"]
    if desc and desc != name:
        parts.append(f"  {desc[:120]}")
    parts.append(f"  obj={obj}  ts={ts}")
    return "".join(parts)


def _format_event_line(e: dict) -> str:
    sev = e.get("severity", "?")
    name = e.get("name", "?")
    obj = e.get("object_name", "")
    ts = e.get("timestamp", "")
    return f"  [{sev}] {name}  obj={obj}  ts={ts}"


def _should_auto_group(items: list[dict]) -> bool:
    """Auto-group when >50% of alarms share the same description."""
    if len(items) < 4:
        return False
    from collections import Counter

    descs = Counter(
        str(a.get("description") or a.get("name") or "") for a in items if isinstance(a, dict)
    )
    if not descs:
        return False
    most_common_count = descs.most_common(1)[0][1]
    return most_common_count > len(items) * 0.5


def _print_grouped_alarms(items: list[dict], key: str = "description") -> None:
    """Print alarms grouped by a field, showing count and affected objects."""
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for a in items:
        if not isinstance(a, dict):
            continue
        group_key = str(a.get(key) or a.get("name") or "Unknown")
        groups[group_key].append(a)

    for desc, group in sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True):
        sev = group[0].get("severity", "?")
        typer.echo(f"  [{sev}] {desc}  (x{len(group)})")
        objects = []
        for a in group[:5]:
            obj = a.get("resolved_name") or a.get("object_name", "?")
            if obj not in objects:
                objects.append(obj)
        for obj in objects:
            typer.echo(f"    {obj}")
        if len(group) > 5:
            typer.echo(f"    ... and {len(group) - 5} more")


@app.command(name="sites")
def list_sites(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List configured UFM sites and aliases."""
    _ensure_init()
    from ufm_mcp.server import ufm_list_sites

    result = ufm_list_sites()
    if json_output:
        _output(result, as_json=True)
    else:
        typer.echo(f"Active: {result.get('active_site')}")
        for s in result.get("sites", []):
            marker = " *" if s.get("active") else ""
            typer.echo(f"  {s['site']}: {s['ufm_url']}{marker}")
        aliases = result.get("aliases", {})
        if aliases:
            typer.echo("Aliases:")
            for alias, target in sorted(aliases.items()):
                typer.echo(f"  {alias} -> {target}")


@app.command()
def version(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get UFM version."""
    _ensure_init()
    from ufm_mcp.server import ufm_get_version

    result = ufm_get_version(site=site)
    if json_output:
        _output(result, as_json=True)
    else:
        typer.echo(f"UFM version: {result.get('version', '?')}")


@app.command()
def concerns(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    lookback: int = typer.Option(30, "--lookback", "-l", help="Lookback window in minutes"),
    max_items: int = typer.Option(10, "--max", "-m", help="Max items per category"),
    no_logs: bool = typer.Option(False, "--no-logs", help="Skip log analysis"),
    no_ber: bool = typer.Option(False, "--no-ber", help="Skip high-BER check"),
    no_links: bool = typer.Option(False, "--no-links", help="Skip link check"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """One-call cluster triage: alarms, events, logs, high-BER, links.

    This is the primary entry point — START HERE for any fabric investigation.
    """
    _ensure_init()
    from ufm_mcp.server import ufm_get_cluster_concerns

    result = ufm_get_cluster_concerns(
        lookback_minutes=lookback,
        include_logs=not no_logs,
        include_high_ber=not no_ber,
        include_links=not no_links,
        log_length=10000,
        max_items=max_items,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"=== Cluster Concerns (last {lookback}min) ===")
    typer.echo(f"Time: {result.get('now_utc', '?')} UTC")
    typer.echo()

    alarms = result.get("alarms", {})
    typer.echo(
        f"Alarms: {alarms.get('total', 0)} total, {alarms.get('non_info_count', 0)} non-info"
    )
    if alarms.get("severity_counts"):
        typer.echo(f"  Severity: {alarms['severity_counts']}")
    for a in alarms.get("non_info_sample", []):
        typer.echo(_format_alarm_line(a))

    typer.echo()
    events = result.get("events", {})
    typer.echo(
        f"Events: {events.get('total', 0)} total, "
        f"{events.get('recent_non_info_count', 0)} recent non-info"
    )
    if events.get("recent_severity_counts"):
        typer.echo(f"  Severity: {events['recent_severity_counts']}")
    for e in events.get("recent_sample", []):
        typer.echo(_format_event_line(e))

    if "logs" in result:
        typer.echo()
        log_data = result["logs"]
        typer.echo(f"Logs (tz={log_data.get('server_tz', '?')}):")
        for lt in ("UFM", "SM"):
            ld = log_data.get(lt, {})
            typer.echo(
                f"  {lt}: {ld.get('recent_lines_count', 0)} recent lines, "
                f"{ld.get('recent_errorish_lines_count', 0)} errors"
            )
            for line in ld.get("recent_errorish_tail", [])[-5:]:
                typer.echo(f"    {line[:200]}")

    if "high_ber" in result:
        typer.echo()
        hb = result["high_ber"]
        typer.echo(
            f"High BER: {hb.get('high_ber_ports_current_count', 0)} ports, "
            f"{hb.get('high_ber_ports_with_recent_activity', 0)} with recent activity"
        )
        if hb.get("high_ber_ports_severity_counts"):
            typer.echo(f"  Severity: {hb['high_ber_ports_severity_counts']}")

    if "links" in result:
        typer.echo()
        lk = result.get("links", {}).get("links", {})
        typer.echo(
            f"Links: {lk.get('total_links', 0)} total, {lk.get('non_info_count', 0)} non-info"
        )
        if lk.get("severity_counts"):
            typer.echo(f"  Severity: {lk['severity_counts']}")


@app.command()
def alarms(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max alarms"),
    group_by: str | None = typer.Option(
        None, "--group-by", "-g", help="Group alarms by field (e.g. description, name)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List active UFM alarms with GUID-to-hostname resolution."""
    _ensure_init()
    from ufm_mcp.helpers import build_guid_to_hostname_map

    client = sites.get_client(site)
    cfg = sites.get_config(site)
    raw = client.get_json(f"{cfg.ufm_api_base_path}/app/alarms")
    items = raw if isinstance(raw, list) else []
    items = items[:limit]

    systems = client.get_json(f"{cfg.ufm_resources_base_path}/resources/systems")
    guid_map = build_guid_to_hostname_map(systems if isinstance(systems, list) else [])

    for a in items:
        if isinstance(a, dict):
            obj = str(a.get("object_name") or "").strip()
            parts = obj.split("_")
            guid_part = parts[0].lower() if parts else obj.lower()
            resolved = guid_map.get(guid_part) or guid_map.get(obj.lower())
            if resolved:
                a["resolved_name"] = resolved

    if json_output:
        _output({"count": len(items), "alarms": ensure_json_serializable(items)}, as_json=True)
        return

    sev_counts = count_severities(items)
    typer.echo(f"# {len(items)} alarm(s)  severity={sev_counts}")

    if group_by or _should_auto_group(items):
        _print_grouped_alarms(items, group_by or "description")
    else:
        for a in items:
            if isinstance(a, dict):
                typer.echo(_format_alarm_line(a))


@app.command()
def events(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    severity: str | None = typer.Option(None, "--severity", help="Filter by severity"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max events"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List UFM events."""
    _ensure_init()
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    params: dict[str, Any] = {}
    if severity:
        params["severity"] = severity
    raw = client.get_json(f"{cfg.ufm_api_base_path}/app/events", params=params or None)
    items = raw if isinstance(raw, list) else []
    items = items[:limit]

    if json_output:
        _output({"count": len(items), "events": ensure_json_serializable(items)}, as_json=True)
        return

    sev_counts = count_severities(items)
    typer.echo(f"# {len(items)} event(s)  severity={sev_counts}")
    for e in items:
        if isinstance(e, dict):
            typer.echo(_format_event_line(e))


@app.command()
def ber(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    lookback: int = typer.Option(30, "--lookback", "-l", help="Lookback window in minutes"),
    max_ports: int = typer.Option(20, "--max", "-m", help="Max ports to show"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Summarize high-BER (Bit Error Rate) ports with recent activity."""
    _ensure_init()
    from ufm_mcp.server import ufm_check_high_ber_recent

    result = ufm_check_high_ber_recent(
        lookback_minutes=lookback,
        max_ports=max_ports,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"=== High BER Ports (last {lookback}min) ===")
    typer.echo(f"Current high-BER ports: {result.get('high_ber_ports_current_count', 0)}")
    typer.echo(f"With recent activity: {result.get('high_ber_ports_with_recent_activity', 0)}")
    if result.get("high_ber_ports_severity_counts"):
        typer.echo(f"Severity: {result['high_ber_ports_severity_counts']}")

    for p in result.get("top_ports_by_recent_events", []):
        sev = p.get("high_ber_severity", "?")
        port = p.get("port", "?")
        sys_name = p.get("system_name", "")
        evts = p.get("event_count", 0)
        alms = p.get("alarm_count", 0)
        typer.echo(f"  [{sev}] {port}  system={sys_name}  events={evts}  alarms={alms}")


@app.command()
def ports(
    system: str | None = typer.Argument(
        None, help="System name or GUID (omit when using --port-guid or --node-guid)"
    ),
    port_numbers: str | None = typer.Argument(
        None, help="Comma-separated port numbers (e.g. 63,64). Omit to list all."
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    lookback: int = typer.Option(15, "--lookback", "-l", help="Lookback window in minutes"),
    port_guid: str | None = typer.Option(
        None, "--port-guid", help="Port GUID (e.g. 0xa088c20300556b96 from `ibstat`)."
    ),
    node_guid: str | None = typer.Option(
        None, "--node-guid", help="HCA/system node GUID; bypasses system-name resolution."
    ),
    errors_only: bool = typer.Option(
        False, "--errors-only", help="Only show ports with non-Info severity"
    ),
    down_only: bool = typer.Option(
        False, "--down-only", help="Only show ports whose physical_state is not Active"
    ),
    logs_all: bool = typer.Option(
        False,
        "--logs-all",
        help="Show full log error_lines_tail (default: filter to lines mentioning the queried system or its port GUIDs)",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Check port health + recent logs/events for ports on a system.

    Selectors (provide exactly one):
      - SYSTEM positional       -- system name or GUID
      - --port-guid <guid>      -- pivot from `ibstat` Port GUID
      - --node-guid <guid>      -- HCA/system node GUID

    Specify port numbers to inspect specific ports, or omit to list all.
    Use --errors-only / --down-only to filter large results.
    """
    _ensure_init()
    from ufm_mcp.server import ufm_check_ports_recent

    selectors = sum(bool(x) for x in (system, port_guid, node_guid))
    if selectors != 1:
        typer.echo("Error: provide exactly one of: SYSTEM, --port-guid, --node-guid", err=True)
        raise typer.Exit(2)

    nums = [int(p.strip()) for p in port_numbers.split(",") if p.strip()] if port_numbers else None
    result = ufm_check_ports_recent(
        system=system or "",
        port_numbers=nums,
        port_guid=port_guid,
        node_guid=node_guid,
        lookback_minutes=lookback,
        errors_only=errors_only,
        down_only=down_only,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok"):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    health = result.get("health", {})
    sys_info = health.get("system", {})
    port_count = len(health.get("ports", []))
    typer.echo(f"=== Port Health: {sys_info.get('system_name', system)} ({port_count} ports) ===")
    typer.echo(f"Model: {sys_info.get('model', '?')}  State: {sys_info.get('state', '?')}")
    typer.echo()

    for p in health.get("ports", []):
        typer.echo(f"Port {p.get('number')}: {p.get('dname', '?')}")
        typer.echo(f"  Physical: {p.get('physical_state')}  Logical: {p.get('logical_state')}")

        fec_str = p.get("fec_mode") or "N/A"
        spd = p.get("active_speed")
        wid = p.get("active_width")
        typer.echo(f"  Speed: {spd}  Width: {wid}  FEC: {fec_str}")

        ber_val = p.get("effective_ber")
        ber_str = str(ber_val) if ber_val not in (None, "", 0, "0") else "0"
        fec_uncorr = p.get("fec_uncorrectable")
        fec_corr = p.get("fec_correctable")
        ber_sev = p.get("high_ber_severity") or "none"
        has_ber_data = (
            ber_val not in (None, "", 0, "0") or fec_uncorr or fec_corr or ber_sev != "none"
        )
        if has_ber_data:
            u = fec_uncorr if fec_uncorr is not None else "N/A"
            c = fec_corr if fec_corr is not None else "N/A"
            parts = [f"  BER: {ber_str} (effective)"]
            parts.append(f"FEC uncorr: {u}")
            parts.append(f"FEC corr: {c}")
            if ber_sev != "none":
                parts.append(f"severity: {ber_sev}")
            typer.echo("  ".join(parts))

        sym_err = p.get("symbol_error_counter")
        link_down = p.get("link_down_counter")
        if sym_err is not None or link_down is not None:
            sv = sym_err if sym_err is not None else "N/A"
            ld = link_down if link_down is not None else "N/A"
            typer.echo(f"  Errors: symbol={sv}  link_down={ld}")

        remote_desc = p.get("remote_node_desc") or p.get("peer_node_name")
        remote_guid = p.get("remote_guid") or p.get("peer_guid")
        if remote_desc or remote_guid:
            if remote_guid and len(remote_guid) >= 5:
                guid_short = remote_guid[-5:]
            else:
                guid_short = remote_guid
            peer_dname = p.get("peer_port_dname", "")
            remote_line = f"  Remote: {remote_desc or '?'}"
            if guid_short:
                remote_line += f" (GUID: ..{guid_short})"
            if peer_dname:
                remote_line += f" port {peer_dname}"
            typer.echo(remote_line)

        for a in p.get("alarms", []):
            sev = a.get("severity")
            nm = a.get("name")
            desc = a.get("description", "")[:100]
            typer.echo(f"  ALARM: [{sev}] {nm} - {desc}")

    log_data = result.get("logs", {})
    if log_data:
        # Build a filter set: queried system identity + the names/GUIDs of ports in the result
        health = result.get("health", {})
        sys_block = health.get("system", {}) or {}
        filter_tokens: set[str] = set()
        for k in ("system_name", "guid"):
            v = str(sys_block.get(k, "")).strip().lower()
            if v:
                filter_tokens.add(v)
        # Selector text (system positional or guid flag values) helps when the
        # system block is partial (sidedoor mode).
        if system:
            filter_tokens.add(system.strip().lower())
        if port_guid:
            filter_tokens.add(port_guid.strip().lower())
        if node_guid:
            filter_tokens.add(node_guid.strip().lower())
        for p in health.get("ports", []) or []:
            for k in ("name", "guid", "system_guid"):
                v = str(p.get(k, "")).strip().lower()
                if v:
                    filter_tokens.add(v)

        typer.echo()
        for lt, ld in log_data.items():
            if not isinstance(ld, dict):
                continue
            typer.echo(
                f"Logs ({lt}): {ld.get('token_match_count', 0)} matches, "
                f"{ld.get('error_lines_count', 0)} errors"
            )
            tail = ld.get("error_lines_tail", []) or []
            if logs_all:
                display = tail[-5:]
            elif filter_tokens:
                display = [
                    line for line in tail if any(tok in str(line).lower() for tok in filter_tokens)
                ][-5:]
            else:
                display = []  # no tokens to filter on → suppress (counts already shown)
            for line in display:
                typer.echo(f"  {line[:200]}")
            if not logs_all and tail and len(display) < len(tail):
                filtered_count = len(tail) - len(display)
                typer.echo(f"  (filtered out {filtered_count} unrelated lines; --logs-all to see)")


@app.command(name="inventory-doctor")
def inventory_doctor_cmd(
    system: str = typer.Argument(help="System name to diagnose"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Diagnose stale-anchor / ghost-port drift for a UFM system."""
    _ensure_init()
    from ufm_mcp.server import ufm_inventory_doctor

    result = ufm_inventory_doctor(system=system, site=site)
    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok"):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    sys_block = result["system"]
    counts = result["counts"]
    diag = result["inferred_diagnosis"]

    typer.echo(f"=== Inventory Doctor: {sys_block['name']} ===")
    typer.echo(f"Anchor GUID: {sys_block['anchor_guid']}")
    typer.echo(
        f"Counts: record_ports={counts['record_ports']}  "
        f"ports_by_name={counts['ports_by_name']}  "
        f"ports_by_guid={counts['ports_by_guid']}"
    )

    if result["ghost_ports"]:
        typer.echo(f"Ghost ports (in record but not on host): {result['ghost_ports']}")
    if result["name_only_ports"]:
        typer.echo(f"Name-only ports (on host but not anchored): {result['name_only_ports']}")

    typer.echo(f"Diagnosis: {diag}")
    typer.echo(f"Remediation: {result['remediation_hint']}")


@app.command()
def logs(
    query: str = typer.Argument(help="Substring or regex to search for"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    log_types: str | None = typer.Option(
        None, "--types", "-t", help="Comma-separated log types (UFM,SM,Event)"
    ),
    length: int = typer.Option(10000, "--length", help="Lines per log type"),
    max_matches: int = typer.Option(50, "--max", "-m", help="Max matches"),
    context: int = typer.Option(2, "--context", "-C", help="Context lines around matches"),
    regex: bool = typer.Option(False, "--regex", "-r", help="Treat query as regex"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Search across UFM/SM logs."""
    _ensure_init()
    from ufm_mcp.server import ufm_search_logs

    types_list = [t.strip() for t in log_types.split(",")] if log_types else None
    result = ufm_search_logs(
        query=query,
        log_types=types_list,
        length=length,
        max_matches=max_matches,
        context_lines=context,
        regex=regex,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"=== Log Search: '{query}' ===")
    typer.echo(
        f"Matches: {result.get('match_count', 0)}  truncated={result.get('truncated', False)}"
    )
    if result.get("counts_by_log_type"):
        typer.echo(f"By type: {result['counts_by_log_type']}")
    typer.echo()

    for hit in result.get("matches", []):
        typer.echo(f"[{hit.get('log_type')}:{hit.get('line_number')}] {hit.get('line', '')[:300]}")


@app.command()
def links(
    system: str | None = typer.Argument(
        None,
        help="Optional system name or GUID. When provided, only links involving this system are shown.",
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    lookback: int = typer.Option(15, "--lookback", "-l", help="Lookback window in minutes"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Summarize link health and recent link-related alarms/events.

    Examples:
      ufm-cli links --site ori                         # all fabric links
      ufm-cli links hci-oh1-su1-ibl08 --site ori      # only links involving this switch
      ufm-cli links a088c20300884bcc --site ori       # filter by GUID
    """
    _ensure_init()
    from ufm_mcp.server import ufm_check_links_recent

    result = ufm_check_links_recent(
        lookback_minutes=lookback,
        site=site,
    )
    if system:
        # Client-side filter: keep only links that mention the system name OR guid
        # in any of the source/destination identity fields.
        sys_q = system.strip().lower()

        def _matches(link: dict) -> bool:
            for key in (
                "source_port_node_description",
                "destination_port_node_description",
                "source_guid",
                "destination_guid",
                "source_system_name",
                "destination_system_name",
            ):
                v = str(link.get(key, "")).lower()
                if v and sys_q in v:
                    return True
            return False

        lk = result.get("links", {})
        if "non_info_links" in lk:
            lk["non_info_links"] = [link for link in lk["non_info_links"] if _matches(link)]

    if json_output:
        _output(result, as_json=True)
        return

    lk = result.get("links", {})
    typer.echo(f"=== Link Health (last {lookback}min) ===")
    typer.echo(f"Total links: {lk.get('total_links', 0)}  Non-info: {lk.get('non_info_count', 0)}")
    if lk.get("severity_counts"):
        typer.echo(f"Severity: {lk['severity_counts']}")

    for link_item in lk.get("non_info_links", [])[:20]:
        src = link_item.get("source_port_node_description", "?")
        dst = link_item.get("destination_port_node_description", "?")
        sev = link_item.get("severity", "?")
        typer.echo(f"  [{sev}] {src} -> {dst}")

    re_events = result.get("recent_link_events", {})
    if re_events.get("count", 0):
        typer.echo(f"\nRecent link events: {re_events['count']}")
        for e in re_events.get("events", [])[:10]:
            typer.echo(_format_event_line(e))

    re_alarms = result.get("recent_link_alarms", {})
    if re_alarms.get("count", 0):
        typer.echo(f"\nRecent link alarms: {re_alarms['count']}")
        for a in re_alarms.get("alarms", [])[:10]:
            typer.echo(_format_alarm_line(a))


@app.command()
def switches(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    errors_only: bool = typer.Option(
        False, "--errors-only", help="Only show switches with non-Info severity"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all InfiniBand switches with health summary."""
    _ensure_init()
    from ufm_mcp.server import ufm_list_switches

    result = ufm_list_switches(site=site, errors_only=errors_only)
    if json_output:
        _output(result, as_json=True)
        return

    switch_list = result.get("switches", [])
    sev_counts = result.get("severity_counts", {})
    typer.echo(f"# {len(switch_list)} switch(es)  severity={sev_counts}")
    for sw in switch_list:
        name = sw.get("system_name", "?")
        guid = sw.get("guid", "?")
        model = sw.get("model", "?")
        state = sw.get("state", "?")
        sev = sw.get("severity", "?")
        ports = sw.get("total_ports", 0)
        typer.echo(
            f"  {name}  GUID={guid}  model={model}  state={state}  severity={sev}  ports={ports}"
        )


@app.command()
def pkeys(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List all InfiniBand partition keys (pkeys)."""
    _ensure_init()
    from ufm_mcp.server import ufm_list_pkeys

    result = ufm_list_pkeys(site=site)
    if json_output:
        _output(result, as_json=True)
        return

    pkey_list = result.get("pkeys", [])
    typer.echo(f"# {len(pkey_list)} pkey(s)")
    for p in pkey_list:
        if isinstance(p, str):
            typer.echo(f"  {p}")
        elif isinstance(p, dict):
            typer.echo(f"  {p.get('pkey', p)}")
        else:
            typer.echo(f"  {p}")


@app.command()
def pkey(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    no_guids: bool = typer.Option(False, "--no-guids", help="Skip GUID membership data"),
    resolve_hosts: bool = typer.Option(
        False, "--resolve-hosts", "-r", help="Resolve GUIDs to hostnames"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get details for a specific partition key."""
    _ensure_init()

    if resolve_hosts:
        from ufm_mcp.server import ufm_get_pkey_hosts

        result = ufm_get_pkey_hosts(pkey=pkey_value, site=site)
        if json_output:
            _output(result, as_json=True)
            return
        _print_pkey_hosts(pkey_value, result)
        return

    from ufm_mcp.server import ufm_get_pkey

    result = ufm_get_pkey(pkey=pkey_value, guids_data=not no_guids, site=site)
    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"=== Pkey {pkey_value} ===")
    data = result.get("data", {})
    if isinstance(data, dict):
        _print_dict(data)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _print_dict(item)
                typer.echo()
            else:
                typer.echo(str(item))
    else:
        typer.echo(str(data))


@app.command(name="pkey-hosts")
def pkey_hosts(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show pkey membership resolved to hostnames (convenience alias for pkey --resolve-hosts)."""
    _ensure_init()
    from ufm_mcp.server import ufm_get_pkey_hosts

    result = ufm_get_pkey_hosts(pkey=pkey_value, site=site)
    if json_output:
        _output(result, as_json=True)
        return
    _print_pkey_hosts(pkey_value, result)


def _print_pkey_hosts(pkey_value: str, result: dict) -> None:
    """Format pkey host resolution output."""
    typer.echo(f"=== Pkey {pkey_value} — Host View ===")
    typer.echo(
        f"Total GUIDs: {result.get('total_guids', 0)}  "
        f"Hosts: {result.get('hosts_count', 0)}  "
        f"Unresolved: {result.get('unresolved_count', 0)}"
    )
    typer.echo()
    for h in result.get("hosts", []):
        membership = ", ".join(h.get("membership_types", []))
        typer.echo(f"  {h['hostname']:40s}  guids={h['guid_count']}  membership={membership}")
    unresolved = result.get("unresolved", [])
    if unresolved:
        typer.echo(f"\n  Unresolved ({len(unresolved)}):")
        for u in unresolved[:20]:
            typer.echo(f"    {u['guid']}  membership={u['membership']}")
        if len(unresolved) > 20:
            typer.echo(f"    ... and {len(unresolved) - 20} more")


@app.command(name="pkey-add-guids")
def pkey_add_guids(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    guids: str = typer.Argument(help="Comma-separated GUIDs to add"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    membership: str = typer.Option("full", "--membership", "-m", help="full or limited"),
    no_ip_over_ib: bool = typer.Option(False, "--no-ip-over-ib", help="Disable IP over IB"),
    index0: bool = typer.Option(False, "--index0", help="Set pkey at index 0"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Add GUIDs to a partition key."""
    _ensure_init()
    from ufm_mcp.server import ufm_add_guids_to_pkey

    guid_list = [g.strip() for g in guids.split(",") if g.strip()]
    result = ufm_add_guids_to_pkey(
        pkey=pkey_value,
        guids=guid_list,
        membership=membership,
        ip_over_ib=not no_ip_over_ib,
        index0=index0,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if result.get("ok"):
        typer.echo(f"Added {result.get('guids_added', 0)} GUID(s) to pkey {pkey_value}")
    else:
        typer.echo(f"Error: {result}", err=True)
        raise typer.Exit(1)


@app.command(name="pkey-remove-guids")
def pkey_remove_guids(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    guids: str = typer.Argument(help="Comma-separated GUIDs to remove"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Remove GUIDs from a partition key."""
    _ensure_init()
    from ufm_mcp.server import ufm_remove_guids_from_pkey

    guid_list = [g.strip() for g in guids.split(",") if g.strip()]
    result = ufm_remove_guids_from_pkey(
        pkey=pkey_value,
        guids=guid_list,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if result.get("ok"):
        typer.echo(f"Removed {result.get('guids_removed', 0)} GUID(s) from pkey {pkey_value}")
    else:
        typer.echo(f"Error: {result}", err=True)
        raise typer.Exit(1)


@app.command(name="pkey-remove-hosts")
def pkey_remove_hosts(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    hosts: str = typer.Argument(help="Comma-separated hostnames to remove"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Remove hosts (and all their GUIDs) from a partition key."""
    _ensure_init()
    from ufm_mcp.server import ufm_remove_hosts_from_pkey

    host_list = [h.strip() for h in hosts.split(",") if h.strip()]
    result = ufm_remove_hosts_from_pkey(
        pkey=pkey_value,
        hosts=host_list,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if result.get("ok"):
        typer.echo(f"Removed {result.get('hosts_removed', 0)} host(s) from pkey {pkey_value}")
    else:
        typer.echo(f"Error: {result}", err=True)
        raise typer.Exit(1)


@app.command(name="pkey-add-hosts")
def pkey_add_hosts(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    hosts: str = typer.Argument(help="Comma-separated hostnames to add"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    membership: str = typer.Option("full", "--membership", "-m", help="full or limited"),
    no_ip_over_ib: bool = typer.Option(False, "--no-ip-over-ib", help="Disable IP over IB"),
    index0: bool = typer.Option(False, "--index0", help="Set pkey at index 0"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Add hosts to a partition key by hostname."""
    _ensure_init()
    from ufm_mcp.server import ufm_add_hosts_to_pkey

    host_list = [h.strip() for h in hosts.split(",") if h.strip()]
    result = ufm_add_hosts_to_pkey(
        pkey=pkey_value,
        hosts=host_list,
        membership=membership,
        ip_over_ib=not no_ip_over_ib,
        index0=index0,
        site=site,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if result.get("ok"):
        msg = f"Added {result.get('hosts_added', 0)} host(s) to pkey {pkey_value}"
        if result.get("fallback_used"):
            msg += f"  [fallback={result['fallback_used']}]"
            if result.get("guids_added"):
                msg += f"  guids={result['guids_added']}"
        if result.get("unresolved_hosts"):
            msg += f"\n  Warning: unresolved hosts: {', '.join(result['unresolved_hosts'])}"
        typer.echo(msg)
    else:
        phase = result.get("error_phase", "unknown")
        error = result.get("error", "unknown error")
        hint = result.get("hint", "")
        typer.echo(f"Error [{phase}]: {error}", err=True)
        if hint:
            typer.echo(f"Hint: {hint}", err=True)
        raise typer.Exit(1)


@app.command(name="pkey-diff")
def pkey_diff_cmd(
    pkey_value: str = typer.Argument(help="Partition key hex value (e.g. 0x1)"),
    expected: str = typer.Option(
        ..., "--expected", "-e", help="Comma-separated expected hostnames"
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    apply: bool = typer.Option(False, "--apply", help="Apply changes (add missing hosts)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when applying"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Compare pkey membership against an expected host list.

    Shows hosts to add, remove, and unchanged. Use --apply to add missing hosts.
    """
    _ensure_init()
    from ufm_mcp.server import ufm_pkey_diff

    expected_hosts = [h.strip() for h in expected.split(",") if h.strip()]
    result = ufm_pkey_diff(pkey=pkey_value, expected_hosts=expected_hosts, site=site)

    if json_output:
        _output(result, as_json=True)
        if not apply:
            return

    if not result.get("ok"):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    if not json_output:
        typer.echo(f"=== Pkey {pkey_value} Diff ===")
        typer.echo(
            f"Current: {result['current_hosts_count']}  Expected: {result['expected_hosts_count']}"
        )
        typer.echo()

        to_add = result.get("to_add", [])
        to_remove = result.get("to_remove", [])
        unchanged = result.get("unchanged", [])

        if to_add:
            typer.echo(f"  + To add ({len(to_add)}):")
            for h in to_add:
                typer.echo(f"    + {h}")
        if to_remove:
            typer.echo(f"  - To remove ({len(to_remove)}):")
            for h in to_remove:
                typer.echo(f"    - {h}")
        if unchanged:
            typer.echo(f"  = Unchanged ({len(unchanged)})")

        if not to_add and not to_remove:
            typer.echo("  Already in sync.")

    if apply:
        to_add = result.get("to_add", [])
        to_remove = result.get("to_remove", [])

        if to_add:
            if not yes:
                typer.confirm(f"Add {len(to_add)} host(s) to pkey {pkey_value}?", abort=True)
            from ufm_mcp.server import ufm_add_hosts_to_pkey

            add_result = ufm_add_hosts_to_pkey(pkey=pkey_value, hosts=to_add, site=site)
            if add_result.get("ok"):
                typer.echo(f"Added {add_result.get('hosts_added', 0)} host(s) to pkey {pkey_value}")
            else:
                typer.echo(f"Error adding hosts: {add_result.get('error', 'unknown')}", err=True)
                raise typer.Exit(1)

        if to_remove:
            n = len(to_remove)
            typer.echo(
                f"Warning: {n} host(s) in to_remove were NOT removed "
                f"(removal requires ufm-cli pkey-remove-guids).",
                err=True,
            )
            result["not_removed"] = to_remove

    if json_output and apply:
        _output(result, as_json=True)


@app.command()
def unhealthy(
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List ports currently flagged as unhealthy by UFM."""
    _ensure_init()
    from ufm_mcp.server import ufm_list_unhealthy_ports

    result = ufm_list_unhealthy_ports(site=site)
    ports = result.get("unhealthy_ports", []) if isinstance(result, dict) else []

    if json_output:
        _output(result, as_json=True)
        return

    if not ports:
        typer.echo("No ports currently flagged as unhealthy.")
        return

    typer.echo(f"{len(ports)} port(s) flagged as unhealthy:")
    for p in ports:
        if isinstance(p, dict):
            _print_dict(p, indent=1)
            typer.echo()
        else:
            typer.echo(f"  {p}")


# ================================================================
#  Topaz fabric health commands
# ================================================================


def _resolve_topaz_az(site_name: str) -> str:
    """Resolve a site name to a Topaz AZ identifier."""
    _ensure_init()
    az_map = _cli_settings.topaz_az_map
    az_id = az_map.get(site_name) or az_map.get(site_name.lower())
    if not az_id:
        typer.echo(
            f"Error: unknown site '{site_name}' for Topaz. "
            f"Known sites: {', '.join(sorted(az_map.keys()))}",
            err=True,
        )
        raise typer.Exit(1)
    return az_id


def _get_topaz_client():
    _ensure_init()
    from ufm_mcp.topaz_client import TopazClient

    return TopazClient(_cli_settings.topaz_endpoint)


@app.command(name="topaz-health")
def topaz_health(
    site_name: str = typer.Option(..., "--site", "-s", help="Site name (e.g. ori, 5c_oh1)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get Topaz fabric health summary for a site."""
    _ensure_init()
    az_id = _resolve_topaz_az(site_name)
    client = _get_topaz_client()
    try:
        result = client.get_fabric_health(az_id)
    finally:
        client.close()

    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok", True):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        if result.get("grpc_details"):
            typer.echo(f"Details: {result['grpc_details']}", err=True)
        raise typer.Exit(1)

    status = result.get("status", "UNKNOWN")
    score = result.get("score", "?")
    typer.echo(f"=== Topaz Fabric Health: {site_name} (AZ: {az_id}) ===")
    typer.echo(f"Status: {status}  Score: {score}")

    summary = result.get("fabric_summary", {})
    if summary:
        typer.echo(
            f"Nodes: {summary.get('total_nodes', 0)}  "
            f"Switches: {summary.get('switches', 0)}  "
            f"CAs: {summary.get('channel_adapters', 0)}  "
            f"Links: {summary.get('total_links', 0)}"
        )

    errors = result.get("total_errors", 0)
    warnings_count = result.get("total_warnings", 0)
    typer.echo(f"Errors: {errors}  Warnings: {warnings_count}")

    for err_type in result.get("errors_by_type", []):
        typer.echo(f"  {err_type.get('error_type', '?')}: {err_type.get('count', 0)}")

    for issue in result.get("issue_summary", []):
        typer.echo(f"  ! {issue}")

    for port in result.get("problematic_ports", [])[:10]:
        typer.echo(
            f"  Port {port.get('port_name', '?')} "
            f"(GUID={port.get('guid', '?')}): "
            f"errors={port.get('total_errors', 0)} "
            f"issues={port.get('issues', [])}"
        )


@app.command(name="topaz-port-counters")
def topaz_port_counters(
    site_name: str = typer.Option(..., "--site", "-s", help="Site name"),
    guid_filter: str | None = typer.Option(None, "--guid-filter", "-g", help="Filter by GUID"),
    errors_only: bool = typer.Option(False, "--errors-only", help="Only ports with errors"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List Topaz port counters for a site."""
    _ensure_init()
    az_id = _resolve_topaz_az(site_name)
    client = _get_topaz_client()
    try:
        result = client.list_port_counters(az_id, errors_only=errors_only, guid_filter=guid_filter)
    finally:
        client.close()

    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok", True):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    counters = result.get("port_counters", [])
    total = result.get("total_count", len(counters))
    typer.echo(f"=== Port Counters: {site_name} ({total} ports) ===")
    for pc in counters[:50]:
        node = pc.get("node_desc") or pc.get("remote_node_desc") or "?"
        port_id = pc.get("port_name") or pc.get("port", "?")
        errs = pc.get("total_errors", 0)
        link = pc.get("link_state", "?")
        fec = pc.get("fec_mode", "?")
        typer.echo(f"  {node} port={port_id} errors={errs} link={link} fec={fec}")


@app.command(name="topaz-cables")
def topaz_cables(
    site_name: str = typer.Option(..., "--site", "-s", help="Site name"),
    alarms_only: bool = typer.Option(False, "--alarms-only", help="Only cables with alarms"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List Topaz cable/transceiver info for a site."""
    _ensure_init()
    az_id = _resolve_topaz_az(site_name)
    client = _get_topaz_client()
    try:
        result = client.list_cables(az_id, alarms_only=alarms_only)
    finally:
        client.close()

    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok", True):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    cables = result.get("cables", [])
    total = result.get("total_count", len(cables))
    typer.echo(f"=== Cables: {site_name} ({total} cables) ===")
    for c in cables[:50]:
        vendor = c.get("vendor", "?")
        pn = c.get("part_number", "?")
        sn = c.get("serial_number", "?")
        temp = c.get("temperature_c", "?")
        alarms = c.get("latched_alarms", [])
        alarm_str = f"  ALARMS={alarms}" if alarms else ""
        typer.echo(f"  {vendor} {pn} SN={sn} temp={temp}C{alarm_str}")


@app.command(name="topaz-switches")
def topaz_switches(
    site_name: str = typer.Option(..., "--site", "-s", help="Site name"),
    errors_only: bool = typer.Option(False, "--errors-only", help="Only switches with errors"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List Topaz switches for a site."""
    _ensure_init()
    az_id = _resolve_topaz_az(site_name)
    client = _get_topaz_client()
    try:
        result = client.list_switches(az_id, errors_only=errors_only)
    finally:
        client.close()

    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok", True):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    switches = result.get("switches", [])
    total = result.get("total_count", len(switches))
    typer.echo(f"=== Switches: {site_name} ({total} switches) ===")
    for sw in switches[:50]:
        desc = sw.get("description") or sw.get("node_desc") or "?"
        guid = sw.get("guid", "?")
        model = sw.get("model", "?")
        errs = sw.get("total_errors", 0)
        typer.echo(f"  {desc} GUID={guid} model={model} errors={errs}")


@app.command(name="upload-ibdiagnet")
def upload_ibdiagnet_cmd(
    ibdiagnet_path: str = typer.Argument(help="Path to the ibdiagnet tarball (.tar.gz)"),
    site: str = typer.Option(..., "--site", "-s", help="Target site (e.g. ori, 5c_oh1)"),
    filename: str | None = typer.Option(
        None, "--filename", help="Optional filename hint for Topaz logging"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Upload an ibdiagnet tarball to Topaz for server-side parsing.

    After upload, the printed `collection_id` can be passed to
    `ufm-cli topaz-cables --collection <id>` (or topaz-port-counters)
    to query the freshly imported view.
    """
    _ensure_init()
    from ufm_mcp.server import ufm_upload_ibdiagnet

    result = ufm_upload_ibdiagnet(
        site=site,
        ibdiagnet_path=ibdiagnet_path,
        filename=filename,
    )
    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok"):
        typer.echo(f"Upload failed: {result.get('error', result)}", err=True)
        raise typer.Exit(1)

    cid = result.get("collection_id") or result.get("collectionId") or "?"
    typer.echo(
        f"Uploaded {result.get('uploaded_bytes', '?')} bytes from {result.get('source_path')}"
    )
    typer.echo(f"site={site}  az_id={result.get('az_id')}  collection_id={cid}")
    typer.echo(f"Next: ufm-cli topaz-cables --site {site} --collection {cid}")
    typer.echo(f"      ufm-cli topaz-port-counters --site {site} --collection {cid}")


@app.command(name="sites-verify")
def sites_verify_cmd(
    site: str = typer.Argument(help="Site name (alias or canonical) to verify"),
    timeout: float = typer.Option(10.0, "--timeout", help="Probe timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Verify a configured site's URL, auth, and API base path.

    Issues a cheap GET against the version endpoint and classifies the result
    so config issues surface at validation time, not on the first real query.

    Status codes:
      ok               - probe succeeded
      dns_fail         - hostname did not resolve
      tls_fail         - TLS handshake failed (e.g. cert verification)
      auth_fail        - HTTP 401 or 403
      wrong_api_path   - HTTP 404 (api_base_path likely points at the wrong route)
      http_<code>      - any other non-200 status
      timeout          - probe exceeded --timeout seconds

    Env var contract for adding sites:
      UFM_<SITE>_URL                  (required, bare host: https://10.x.y.z/)
      UFM_<SITE>_TOKEN                (Bearer token; alt: UFM_<SITE>_ACCESS_TOKEN)
      UFM_<SITE>_VERIFY_SSL           (default: true)
      UFM_<SITE>_TIMEOUT_SECONDS      (default: 30)
      UFM_<SITE>_API_BASE_PATH        (default: ufmRestV3)
      UFM_<SITE>_RESOURCES_BASE_PATH  (default: ufmRestV3)
      UFM_<SITE>_LOGS_BASE_PATH       (default: ufmRest)
      UFM_<SITE>_WEB_BASE_PATH        (default: ufmRest)
      UFM_<SITE>_BACKUP_BASE_PATH     (default: ufmRest)
      UFM_<SITE>_JOBS_BASE_PATH       (default: ufmRestV3)
      UFM_SITE_ALIASES_JSON           (optional: extra aliases beyond canonical name)
    """
    import socket

    import httpx

    _ensure_init()

    try:
        client = sites.get_client(site)
        cfg = sites.get_config(site)
    except Exception as exc:
        result = {
            "site": site,
            "status": "config_error",
            "error": str(exc),
        }
        if json_output:
            _output(result, as_json=True)
        else:
            typer.echo(f"site={site}  status=config_error  error={exc}")
        raise typer.Exit(1) from exc

    api_base = cfg.ufm_api_base_path or "ufmRestV3"
    probe_path = f"{api_base}/app/ufm_version"

    status: str
    detail: str = ""
    try:
        # Use get_json which calls raise_for_status; catch the HTTPStatusError
        # to classify by status code without needing a separate .get() method.
        client.get_json(probe_path)
        status = "ok"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            status = "auth_fail"
            detail = f"HTTP {code}"
        elif code == 404:
            status = "wrong_api_path"
            detail = f"GET {probe_path} → 404; api_base_path likely incorrect"
        else:
            status = f"http_{code}"
            detail = f"HTTP {code}"
    except socket.gaierror as exc:
        status = "dns_fail"
        detail = str(exc)
    except httpx.ConnectError as exc:
        exc_str = str(exc).lower()
        if "name or service" in exc_str or "nodename" in exc_str:
            status = "dns_fail"
        elif "ssl" in exc_str or "certificate" in exc_str:
            status = "tls_fail"
        else:
            status = "connect_fail"
        detail = str(exc)
    except httpx.TimeoutException as exc:
        status = "timeout"
        detail = str(exc)
    except Exception as exc:
        status = "error"
        detail = f"{type(exc).__name__}: {exc}"

    out = {
        "site": site,
        "url": cfg.ufm_url,
        "api_base_path": api_base,
        "status": status,
        "detail": detail,
    }

    if json_output:
        _output(out, as_json=True)
    else:
        typer.echo(f"site={site}  url={cfg.ufm_url}  api_base={api_base}  status={status}")
        if detail:
            typer.echo(f"  detail: {detail}")

    if status != "ok":
        raise typer.Exit(2)


def main() -> None:
    setup_logging(name="ufm-cli", level="INFO", system_log=True)
    app()


if __name__ == "__main__":
    main()
