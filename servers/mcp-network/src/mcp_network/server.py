"""MCP server entry point for mcp-network.

Exposes read-only tools over a multi-site switch fleet. Non-secret fleet
data lives in ``inventory/sites/*.json``; secrets come from env vars named
by each site's ``credentials_env`` object.

Every tool is registered with :func:`mcp_common.dual_mode.dual_mode_tool`, so
the same function backs both the FastMCP tool and the ``network-cli`` command
synthesized by :func:`mcp_common.dual_mode.build_cli_from_mcp` (see
``mcp_network.cli``). The tools stay strictly read-only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from fastmcp import Context, FastMCP
from mcp_common import (
    HttpAccessTokenAuth,
    MCPSettings,
    add_health_route,
    create_http_app,
    health_resource,
    mcp_remediation_wrapper,
    setup_logging,
)
from mcp_common.dual_mode import dual_mode_tool
from mcp_common.env import load_env
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from mcp_network import __version__
from mcp_network.drivers import NetworkDriverError, get_driver
from mcp_network.inventory import SwitchEntry
from mcp_network.netbox import NetboxUnavailable, get_node_nic_macs
from mcp_network.presets import resolve_preset
from mcp_network.sites import NetworkSiteConfig, NetworkSiteManager

load_env()

PROJECT_REPO = "togethercomputer/mcp-common"


class Settings(MCPSettings):
    """Server settings loaded from environment / ``.env`` file.

    Base ``MCPSettings`` provides transport/logging/HTTP-auth. The only
    service-specific knob is an inventory-dir override (useful for tests).
    All fleet credentials live in env vars whose names are specified in
    each ``inventory/sites/*.json`` ``credentials_env`` block.
    """

    model_config = SettingsConfigDict(env_prefix="MCP_NETWORK_")

    inventory_dir: Path | None = Field(
        default=None,
        description=(
            "Override path to the inventory directory. Defaults to the "
            "'inventory/' dir packaged with this MCP."
        ),
    )


settings = Settings()
log = setup_logging(level=settings.log_level, json_output=settings.log_json, name="mcp_network")

# The site manager is constructed eagerly but loaded **lazily** on first real
# access (see ``NetworkSiteManager.ensure_loaded``). This keeps the
# ``Loaded N site(s)`` INFO log — and the per-site "not operational" WARNING —
# off stderr for introspection paths that never touch the fleet, notably
# ``network-cli --version`` / ``--help`` (mcp-common #95). Both the MCP tools
# and the synthesized CLI commands reach the fleet through ``site_manager``
# accessors (``sites`` / ``get_site`` / ``resolve_switch``), which trigger the
# load on demand. The CLI additionally wires ``before_command=ensure_loaded``
# so the load fires once per real command (never on ``--help`` / ``--version``).
site_manager = NetworkSiteManager()

mcp = FastMCP("mcp-network")
add_health_route(mcp, "mcp-network")

_remediate = mcp_remediation_wrapper(project_repo=PROJECT_REPO, version=__version__)


@mcp.resource("health://mcp-network")
def health() -> dict[str, Any]:
    """Server health and uptime (MCP resource, not HTTP)."""
    return health_resource(name="mcp-network", version=__version__).to_dict()


# ---------------------------------------------------------------------------
# Dual-mode parameter annotations
#
# Each alias carries a Pydantic ``Field`` (drives the MCP input-schema
# description) plus a Typer marker (drives the synthesized ``network-cli``
# surface): ``typer.Argument`` → positional, ``typer.Option`` → flag. FastMCP
# ignores the Typer marker when building the tool schema; Typer ignores the
# ``Field`` — so one signature feeds both surfaces without drift. The positional
# ``switch``/``mac``/``node`` args and the ``-s`` short flag preserve the legacy
# ``network-cli`` command shapes.
# ---------------------------------------------------------------------------

SwitchArg = Annotated[
    str,
    Field(description="Switch hostname or management IP."),
    typer.Argument(help="Switch hostname or management IP."),
]
SiteOpt = Annotated[
    str | None,
    Field(description="Optional site key/alias. Defaults to the configured default site."),
    typer.Option("--site", "-s", help="Site key or alias."),
]


def _site_summary(cfg: NetworkSiteConfig) -> dict[str, Any]:
    return {
        "site": cfg.site,
        "display_name": cfg.inventory.display_name,
        "driver": cfg.driver,
        "operational": cfg.operational,
        "reason": cfg.reason,
        "switch_count": len(cfg.switches),
        "netbox_site_slug": cfg.inventory.netbox_site_slug,
        "mgmt_subnets": cfg.inventory.mgmt_subnets,
    }


def _switch_summary(cfg: NetworkSiteConfig, sw: SwitchEntry) -> dict[str, Any]:
    return {
        "site": cfg.site,
        "name": sw.name,
        "mgmt_ip": sw.mgmt_ip,
        "role": sw.role,
        "model": sw.model,
        "os": sw.os,
        "reachable": sw.reachable,
    }


def _classify_port(cfg: NetworkSiteConfig, sw: SwitchEntry, port: str) -> str:
    """Return 'uplink' | 'downlink' using the site's uplink_ports config."""
    role_uplinks: list[str] = []
    up = cfg.inventory.uplink_ports
    if sw.role == "leaf":
        role_uplinks = up.leaf
    elif sw.role == "spine":
        role_uplinks = up.spine
    elif sw.role == "border":
        role_uplinks = up.border
    # strip breakouts (swp14s1 -> swp14) for matching parent-port rules too
    parent = re.sub(r"s\d+$", "", port)
    if port in role_uplinks or parent in role_uplinks:
        return "uplink"
    return "downlink"


def _resolve_switch_or_raise(
    name_or_ip: str, site: str | None
) -> tuple[NetworkSiteConfig, SwitchEntry]:
    try:
        return site_manager.resolve_switch(name_or_ip, site)
    except KeyError as e:
        raise ValueError(str(e)) from e


# ``list_sites`` / ``list_switches`` are defined first (below), so their two
# human-output formatters live here, ahead of the larger projection +
# formatter block that precedes the per-switch tools. Both are self-contained
# (no dependency on the shared ``_result_header`` / ``_fmt_kv`` helpers).


def _fmt_sites(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    sites = data.get("sites", [])
    lines = [f"default: {data.get('default', '?')}  ({len(sites)} site(s))"]
    for s in sites:
        flag = "" if s.get("operational", True) else "  [non-operational]"
        lines.append(
            f"  {s.get('site', '?')!s:10} {s.get('display_name', '')!s:28} "
            f"driver={s.get('driver', '?')} switches={s.get('switch_count', '?')}{flag}"
        )
    return "\n".join(lines)


def _fmt_switches(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    rows = data.get("switches", [])
    lines = [f"site: {data.get('site', '?')}  ({len(rows)} switch(es))"]
    for s in rows:
        flag = "" if s.get("reachable", True) else "  [unreachable]"
        lines.append(
            f"  {s.get('name', '?')!s:28} {s.get('mgmt_ip', '?')!s:16} "
            f"{s.get('role', '?')!s:7} {s.get('model', '?')}{flag}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@dual_mode_tool(
    mcp,
    name="list_sites",
    cli_name="sites",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_sites},
)
@_remediate
async def list_sites() -> dict[str, Any]:
    """List every site known to this MCP, plus the default site and aliases."""
    return {
        "default": site_manager.default_site,
        "aliases": site_manager.aliases,
        "sites": [_site_summary(cfg) for cfg in site_manager.sites.values()],
    }


@dual_mode_tool(
    mcp,
    name="list_switches",
    cli_name="switches",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_switches},
)
@_remediate
async def list_switches(site: SiteOpt = None) -> dict[str, Any]:
    """List switches in one site (or the default site)."""
    cfg = site_manager.get_site(site)
    return {
        "site": cfg.site,
        "switches": [_switch_summary(cfg, sw) for sw in cfg.switches],
    }


_SYSTEM_INFO_BRIEF_KEYS = frozenset(
    {
        "hostname",
        "fqdn",
        "health",
        "uptime",
        "os-version",
        "model",
        "platform",
        "build",
        "version",
        "system-mac",
    }
)


def _brief_system_info(data: dict[str, Any]) -> dict[str, Any]:
    """Extract only key fields from the raw ``nv show system`` blob."""
    out: dict[str, Any] = {}
    for key in _SYSTEM_INFO_BRIEF_KEYS:
        if key in data:
            out[key] = data[key]
    return out


@dual_mode_tool(
    mcp,
    name="get_system_info",
    cli_name="system-info",
    annotations={"readOnlyHint": True},
    read_only=True,
)
@_remediate
async def get_system_info(
    switch: SwitchArg,
    brief: Annotated[
        bool,
        Field(
            description=(
                "When True (default), return only key fields (hostname, health, "
                "uptime, OS version, model). Set False for the full raw blob."
            )
        ),
    ] = True,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return system information for one switch.

    By default returns a brief summary (hostname, health, uptime, OS version,
    model/platform). Pass ``brief=False`` for the full ``nv show system`` blob.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    data = await drv.system_info()
    return {
        "site": cfg.site,
        "switch": sw.name,
        "mgmt_ip": sw.mgmt_ip,
        "data": _brief_system_info(data) if brief else data,
    }


# ---------------------------------------------------------------------------
# Projection + human-output helpers
#
# These power the token-saving ``brief=True`` defaults (mirroring
# ``get_system_info``) and the ``formatters`` passed to the list-heavy
# ``@dual_mode_tool``s so ``network-cli`` renders compact human output instead
# of a truncated ``str(dict)``. ``--json`` / ``-j`` (and non-TTY stdout) bypass
# the formatters via the shared ``mcp_common.cli.echo_result`` path. Every
# projection is best-effort field extraction tolerant of NVUE field-placement
# differences across Cumulus versions; the full blob stays reachable via
# ``brief=False`` (or a higher ``limit``).
# ---------------------------------------------------------------------------


def _first_present(d: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    """Return the first non-empty value among ``candidates`` keys in ``d``."""
    for key in candidates:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return None


def _scalarize(value: Any) -> Any:
    """Collapse a single-key NVUE enum dict (e.g. ``{"up": {}}``) to its key."""
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value))
    return value


def _brief_port(cfg: NetworkSiteConfig, sw: SwitchEntry, entry: dict[str, Any]) -> dict[str, Any]:
    """Project one ``nv show interface`` entry to the high-signal port fields.

    Tolerates both the flat (``oper-status``) and nested (``link.oper-status``)
    NVUE layouts so the projection survives field-placement differences across
    Cumulus versions.
    """
    name = str(entry.get("name", ""))
    link_value = entry.get("link")
    link: dict[str, Any] = link_value if isinstance(link_value, dict) else {}
    return {
        "name": name,
        "admin": _scalarize(
            _first_present(entry, ("admin-status", "admin-state"))
            or _first_present(link, ("admin-status", "admin-state"))
        ),
        "oper": _scalarize(
            _first_present(entry, ("oper-status", "oper-state"))
            or _first_present(link, ("oper-status", "oper-state", "state"))
        ),
        "speed": _first_present(entry, ("speed",)) or _first_present(link, ("speed",)),
        "classification": _classify_port(cfg, sw, name) if name else None,
    }


def _lldp_neighbor_blobs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidate neighbor sub-objects within a flattened LLDP entry."""
    blobs: list[dict[str, Any]] = [entry]
    lldp = entry.get("lldp")
    if isinstance(lldp, dict):
        blobs.append(lldp)
        blobs.extend(v for v in lldp.values() if isinstance(v, dict))
    elif isinstance(lldp, list):
        blobs.extend(v for v in lldp if isinstance(v, dict))
    return blobs


def _brief_lldp_neighbor(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one flattened LLDP entry to ``{local-port, remote-system, remote-port}``.

    Handles the already-projected shape (keys present verbatim) and the raw NVUE
    ``nv show interface --view lldp`` layout, where the neighbor's system name and
    port live under nested ``chassis`` / ``port`` objects.
    """
    local_port = _first_present(entry, ("local-port", "name"))
    remote_system = _first_present(
        entry,
        ("remote-system", "remote-system-name", "remote-host", "system-name", "hostname"),
    )
    remote_port = _first_present(entry, ("remote-port", "remote-port-id", "port-id"))

    if remote_system is None or remote_port is None:
        for blob in _lldp_neighbor_blobs(entry):
            if remote_system is None:
                chassis = blob.get("chassis")
                if isinstance(chassis, dict):
                    remote_system = _first_present(chassis, ("system-name", "sys-name", "name"))
                if remote_system is None:
                    remote_system = _first_present(
                        blob,
                        ("remote-system", "remote-system-name", "remote-host", "system-name"),
                    )
            if remote_port is None:
                port = blob.get("port")
                if isinstance(port, dict):
                    remote_port = _first_present(
                        port, ("name", "description", "ifname", "port-id", "id")
                    )
                if remote_port is None:
                    remote_port = _first_present(blob, ("remote-port", "port-id"))
            if remote_system is not None and remote_port is not None:
                break

    return {
        "local-port": local_port,
        "remote-system": remote_system,
        "remote-port": remote_port,
    }


_BGP_RX_PREFIX_KEYS = ("rx-prefix", "pfx-rcvd", "prefixes-received", "prefix-received")
_BGP_TX_PREFIX_KEYS = ("tx-prefix", "pfx-sent", "prefixes-sent", "prefix-sent")


def _bgp_prefixes(blob: dict[str, Any]) -> dict[str, Any]:
    """Pull per-address-family prefix counts (rx/tx) from a BGP neighbor blob."""
    families = blob.get("afi-safi")
    if not isinstance(families, dict):
        families = blob.get("address-family")
    out: dict[str, Any] = {}
    if isinstance(families, dict):
        for afi, fam in families.items():
            if not isinstance(fam, dict):
                continue
            rx = _first_present(fam, _BGP_RX_PREFIX_KEYS)
            tx = _first_present(fam, _BGP_TX_PREFIX_KEYS)
            if rx is not None or tx is not None:
                out[afi] = {"rx": rx, "tx": tx}
        return out
    rx = _first_present(blob, _BGP_RX_PREFIX_KEYS)
    tx = _first_present(blob, _BGP_TX_PREFIX_KEYS)
    if rx is not None or tx is not None:
        out = {"rx": rx, "tx": tx}
    return out


def _brief_bgp_neighbors(data: dict[str, Any]) -> dict[str, Any]:
    """Project the raw BGP neighbor blob to a compact per-peer summary.

    On Cumulus Linux 5.12+ ``nv show vrf default router bgp neighbor -o json``
    already returns connection-summary data keyed by neighbor; this trims each
    peer to the high-signal fields (remote-as, state, uptime, prefixes rx/tx).
    Best-effort: any field not found is omitted, and ``brief=False`` returns the
    untouched blob.
    """
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for peer, blob in data.items():
        if not isinstance(blob, dict):
            out[peer] = blob
            continue
        summary: dict[str, Any] = {}
        remote_as = _first_present(blob, ("remote-as", "remote-asn", "asn"))
        if remote_as is not None:
            summary["remote-as"] = _scalarize(remote_as)
        state = _first_present(blob, ("state", "session-state", "bgp-state"))
        if state is not None:
            summary["state"] = _scalarize(state)
        uptime = _first_present(blob, ("up-time", "uptime", "peer-established-time", "peer-uptime"))
        if uptime is not None:
            summary["uptime"] = uptime
        prefixes = _bgp_prefixes(blob)
        if prefixes:
            summary["prefixes"] = prefixes
        out[peer] = summary
    return out


def _fmt_kv(data: Any) -> str:
    """Generic ``key: value`` renderer (nested dicts collapsed to a name/value)."""
    if not isinstance(data, dict):
        return str(data)
    lines: list[str] = []
    for key, value in data.items():
        rendered: Any = value
        if isinstance(value, dict):
            rendered = value.get("name", value.get("display", value))
        elif isinstance(value, list) and len(value) > 5:
            rendered = f"[{len(value)} items]"
        lines.append(f"  {key}: {rendered}")
    return "\n".join(lines)


def _result_header(data: dict[str, Any], label: str, count: int) -> str:
    """Build a ``<switch> <site> <label>: <count>`` (optionally truncated) header."""
    prefix = " ".join(str(p) for p in (data.get("switch"), data.get("site")) if p)
    header = f"{prefix} {label}: {count}".strip()
    shown = data.get("entries")
    if data.get("truncated") and isinstance(shown, list):
        header += f" (showing {len(shown)}; raise limit for more)"
    return header


def _fmt_port_status(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    if "ports" not in data:  # single-port result keeps the raw blob
        return _fmt_kv(data)
    rows = data.get("ports", [])
    lines = [_result_header(data, "ports", len(rows))]
    for p in rows:
        if not isinstance(p, dict):
            lines.append(f"  {p}")
            continue
        lines.append(
            f"  {p.get('name', '?')!s:12} admin={p.get('admin', '?')} "
            f"oper={p.get('oper', '?')} speed={p.get('speed', '?')} "
            f"{p.get('classification', '')}"
        )
    return "\n".join(lines)


def _fmt_lldp(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    rows = data.get("neighbors", [])
    lines = [_result_header(data, "lldp neighbors", len(rows))]
    for n in rows:
        if not isinstance(n, dict):
            lines.append(f"  {n}")
            continue
        local = n.get("local-port", n.get("name", "?"))
        lines.append(
            f"  {local!s:12} -> {n.get('remote-system', '?')} ({n.get('remote-port', '?')})"
        )
    return "\n".join(lines)


def _fmt_bgp(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    peers = data.get("data", {})
    if not isinstance(peers, dict):
        return _fmt_kv(data)
    lines = [_result_header(data, "bgp neighbors", len(peers))]
    for peer, blob in peers.items():
        if not isinstance(blob, dict):
            lines.append(f"  {peer}: {blob}")
            continue
        extras = []
        if "uptime" in blob:
            extras.append(f"uptime={blob['uptime']}")
        if blob.get("prefixes"):
            extras.append(f"prefixes={blob['prefixes']}")
        suffix = (" " + " ".join(extras)) if extras else ""
        lines.append(
            f"  {peer!s:16} as={blob.get('remote-as', '?')} state={blob.get('state', '?')}{suffix}"
        )
    return "\n".join(lines)


def _fmt_mac_table(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    total = data.get("total", 0)
    prefix = " ".join(str(p) for p in (data.get("switch"), data.get("site")) if p)
    if data.get("count_only"):
        return f"{prefix} mac-table: {total} match(es)".strip()
    rows = data.get("entries", [])
    lines = [_result_header(data, "mac-table", total)]
    for e in rows:
        if not isinstance(e, dict):
            lines.append(f"  {e}")
            continue
        lines.append(
            f"  {e.get('mac', '?')!s:20} {e.get('interface', '?')!s:12} "
            f"vlan={e.get('vlan', '?')} age={e.get('age', '?')}"
        )
    return "\n".join(lines)


def _fmt_logs(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    rows = data.get("entries", [])
    lines = [_result_header(data, "log entries", len(rows))]
    for e in rows:
        if not isinstance(e, dict):
            lines.append(f"  {e}")
            continue
        src = e.get("identifier") or e.get("unit") or ""
        lines.append(
            f"  {e.get('timestamp', '?')} {e.get('priority', '?')!s:8} "
            f"{src}: {e.get('message', '')}"
        )
    return "\n".join(lines)


def _fmt_wjh(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    rows = data.get("entries", [])
    lines = [_result_header(data, "wjh drops", data.get("total", len(rows)))]
    for e in rows:
        if not isinstance(e, dict):
            lines.append(f"  {e}")
            continue
        ingress = e.get("ingress-port", e.get("ingress", "?"))
        lines.append(f"  {e.get('reason', '?')!s:24} ingress={ingress} {e.get('drop-type', '')}")
    return "\n".join(lines)


@dual_mode_tool(
    mcp,
    name="get_port_status",
    cli_name="port-status",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_port_status},
)
@_remediate
async def get_port_status(
    switch: SwitchArg,
    port: Annotated[
        str | None,
        Field(description="Port name e.g. 'swp14s1'. Omit for all ports."),
        typer.Argument(help="Port name e.g. 'swp14s1'. Omit for all ports."),
    ] = None,
    brief: Annotated[
        bool,
        Field(
            description=(
                "When True (default) and listing all ports, project each port to "
                "{name, admin, oper, speed, classification}. Set False for the raw "
                "per-port blobs. Ignored when a single ``port`` is given (always raw)."
            )
        ),
    ] = True,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return operational state for one port, or all ports on the switch.

    For the all-ports view, ``brief=True`` (default) returns a compact projection
    (name, admin, oper, speed, uplink/downlink classification); pass
    ``brief=False`` for the full per-port blobs. A single ``port`` always returns
    raw detail.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    if port:
        data = await drv.interface(port)
        return {
            "site": cfg.site,
            "switch": sw.name,
            "port": port,
            "classification": _classify_port(cfg, sw, port),
            "data": data,
        }
    ports = await drv.interfaces_brief()
    if brief:
        ports = [_brief_port(cfg, sw, p) for p in ports]
    return {"site": cfg.site, "switch": sw.name, "ports": ports}


@dual_mode_tool(
    mcp,
    name="get_port_counters",
    cli_name="port-counters",
    annotations={"readOnlyHint": True},
    read_only=True,
)
@_remediate
async def get_port_counters(
    switch: SwitchArg,
    port: Annotated[
        str,
        Field(description="Port name, e.g. 'swp14s1'."),
        typer.Argument(help="Port name, e.g. 'swp14s1'."),
    ],
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return traffic/error/drop/PFC/ECN counters for one port."""
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    data = await drv.interface_counters(port)
    return {
        "site": cfg.site,
        "switch": sw.name,
        "port": port,
        "classification": _classify_port(cfg, sw, port),
        "data": data,
    }


@dual_mode_tool(
    mcp,
    name="get_lldp_neighbors",
    cli_name="lldp",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_lldp},
)
@_remediate
async def get_lldp_neighbors(
    switch: SwitchArg,
    brief: Annotated[
        bool,
        Field(
            description=(
                "When True (default), project each neighbor to "
                "{local-port, remote-system, remote-port}. Set False for the raw "
                "per-interface LLDP blob."
            )
        ),
    ] = True,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return LLDP neighbor table for one switch.

    ``brief=True`` (default) projects each neighbor to
    {local-port, remote-system, remote-port}; ``brief=False`` returns the raw
    per-interface LLDP blob.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    neighbors = await drv.lldp()
    if brief:
        neighbors = [_brief_lldp_neighbor(n) for n in neighbors]
    return {"site": cfg.site, "switch": sw.name, "neighbors": neighbors}


@dual_mode_tool(
    mcp,
    name="get_bgp_neighbors",
    cli_name="bgp",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_bgp},
)
@_remediate
async def get_bgp_neighbors(
    switch: SwitchArg,
    brief: Annotated[
        bool,
        Field(
            description=(
                "When True (default), project each neighbor to the connection "
                "summary {remote-as, state, uptime, prefixes rx/tx}. Set False for "
                "the full ``nv show ... bgp neighbor`` blob."
            )
        ),
    ] = True,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return BGP neighbor summary for one switch (default VRF).

    ``brief=True`` (default) projects each neighbor to the connection summary
    (remote-as, state, uptime, prefixes rx/tx) — the tool is named/documented as
    a *summary*, so a trimmed projection is the sensible default. Pass
    ``brief=False`` for the full per-neighbor blob.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    data = await drv.bgp_summary()
    return {
        "site": cfg.site,
        "switch": sw.name,
        "data": _brief_bgp_neighbors(data) if brief else data,
    }


@dual_mode_tool(
    mcp,
    name="get_mac_table",
    cli_name="mac-table",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_mac_table},
)
@_remediate
async def get_mac_table(
    switch: SwitchArg,
    mac: Annotated[
        str | None,
        Field(
            description="Filter to a specific MAC (any common format; normalized before matching)."
        ),
    ] = None,
    port: Annotated[str | None, Field(description="Filter to a specific port.")] = None,
    vlan: Annotated[int | None, Field(description="Filter to a specific VLAN.")] = None,
    limit: Annotated[
        int,
        Field(description="Max entries to return after filtering (default 200). Raise for more."),
    ] = 200,
    count_only: Annotated[
        bool,
        Field(description="Return only the total match count, omitting the entries list."),
    ] = False,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return the bridge MAC-learning table with optional filters.

    Entries are filtered (mac/port/vlan) then capped at ``limit`` (default 200);
    the response reports ``total`` (matches before the cap) and ``truncated``.
    Pass ``count_only=True`` for just the count. The ``mac`` filter is normalized
    via the same canonicalizer used fleet-wide, so bare-hex (``9c63c026efec``),
    dotted (``9c63.c026.efec``), and colon forms all match.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    entries = await drv.mac_table()

    want_mac = _normalize_mac(mac) if mac else None
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if want_mac and _normalize_mac(str(entry.get("mac", ""))) != want_mac:
            continue
        if port and entry.get("interface") != port:
            continue
        if vlan is not None and entry.get("vlan") != vlan:
            continue
        rows.append(entry)

    total = len(rows)
    if count_only:
        return {"site": cfg.site, "switch": sw.name, "total": total, "count_only": True}
    capped = rows[:limit] if limit >= 0 else rows
    return {
        "site": cfg.site,
        "switch": sw.name,
        "entries": capped,
        "total": total,
        "truncated": total > len(capped),
    }


MAX_PARALLEL_SSH = 4


FIND_PORT_FOR_MAC_DESCRIPTION = """Search every reachable switch in a site for a MAC.

Distinguishes **direct** hits (MAC learned on a physical ``swp*`` port)
from **indirect** hits (learned on a bond/uplink — reached via another
switch). Useful for answering "which switch/port is this NIC cabled to?"

Switches are scanned in parallel (bounded concurrency). Each switch
opens one SSH session for the scan, then closes it.
"""


@dual_mode_tool(
    mcp,
    name="find_port_for_mac",
    cli_name="find-mac",
    annotations={"readOnlyHint": True},
    read_only=True,
    description=FIND_PORT_FOR_MAC_DESCRIPTION,
)
@_remediate
async def find_port_for_mac(
    mac: Annotated[
        str,
        Field(description="MAC address (any common format)."),
        typer.Argument(help="MAC address (any common format)."),
    ],
    site: SiteOpt = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Search every reachable switch in a site for a MAC (direct vs indirect hits)."""
    cfg = site_manager.get_site(site)
    target = _normalize_mac(mac)
    reachable = [sw for sw in cfg.switches if sw.reachable]
    sem = asyncio.Semaphore(MAX_PARALLEL_SSH)
    completed = 0

    async def _scan(sw: SwitchEntry) -> list[dict[str, Any]]:
        nonlocal completed
        async with sem:
            try:
                drv = get_driver(cfg, sw)
                async with drv.session():
                    entries = await drv.mac_table()
            except NetworkDriverError as e:
                return [{"switch": sw.name, "error": str(e)}]
            finally:
                completed += 1
                if ctx is not None:
                    await ctx.report_progress(
                        progress=completed,
                        total=len(reachable),
                        message=f"scanned {sw.name}",
                    )
        return _match_mac(cfg, sw, entries, target)

    results = await asyncio.gather(*[_scan(sw) for sw in reachable])
    flat: list[dict[str, Any]] = [r for sub in results for r in sub]
    direct = [r for r in flat if r.get("direct")]
    indirect = [r for r in flat if r.get("direct") is False]
    errors = [r for r in flat if "error" in r]
    return {
        "site": cfg.site,
        "mac": target,
        "direct": direct,
        "indirect": indirect,
        "errors": errors,
    }


FIND_PORT_FOR_NODE_DESCRIPTION = """Resolve a node's NIC MACs from NetBox, then locate each on the fabric.

Does a **single parallel pass** across all reachable switches to collect
MAC tables, then matches every NIC MAC locally. This means N NICs cost
the same as 1 — only one SSH session per switch regardless of NIC count.

Requires ``netbox-cli`` on PATH.
"""


@dual_mode_tool(
    mcp,
    name="find_port_for_node",
    cli_name="find-node",
    annotations={"readOnlyHint": True},
    read_only=True,
    description=FIND_PORT_FOR_NODE_DESCRIPTION,
)
@_remediate
async def find_port_for_node(
    node: Annotated[
        str,
        Field(description="Node hostname as tracked in NetBox, e.g. 'research-common-h100-078'."),
        typer.Argument(help="Node hostname as tracked in NetBox, e.g. 'research-common-h100-078'."),
    ],
    site: SiteOpt = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Resolve a node's NIC MACs from NetBox, then locate each on the fabric (requires netbox-cli)."""
    try:
        nics = await get_node_nic_macs(node)
    except NetboxUnavailable as e:
        return {
            "node": node,
            "error": str(e),
            "hint": "install netbox-cli or set NETBOX_URL/NETBOX_TOKEN in env",
        }

    if not nics:
        return {"node": node, "nics": [], "note": "NetBox returned no interfaces with MACs"}

    cfg = site_manager.get_site(site)
    reachable = [sw for sw in cfg.switches if sw.reachable]
    sem = asyncio.Semaphore(MAX_PARALLEL_SSH)
    completed = 0

    async def _collect(
        sw: SwitchEntry,
    ) -> tuple[SwitchEntry, list[dict[str, Any]] | str]:
        nonlocal completed
        async with sem:
            try:
                drv = get_driver(cfg, sw)
                async with drv.session():
                    entries = await drv.mac_table()
            except NetworkDriverError as e:
                return sw, str(e)
            finally:
                completed += 1
                if ctx is not None:
                    await ctx.report_progress(
                        progress=completed,
                        total=len(reachable),
                        message=f"scanned {sw.name}",
                    )
        return sw, entries

    results = await asyncio.gather(*[_collect(sw) for sw in reachable])

    out: list[dict[str, Any]] = []
    for nic in nics:
        norm = _normalize_mac(nic["mac"])
        direct: list[dict[str, Any]] = []
        indirect: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for sw, data in results:
            if isinstance(data, str):
                errors.append({"switch": sw.name, "error": data})
                continue
            for hit in _match_mac(cfg, sw, data, norm):
                if hit.get("direct"):
                    direct.append(hit)
                elif hit.get("direct") is False:
                    indirect.append(hit)
        out.append(
            {
                "nic": nic["name"],
                "mac": nic["mac"],
                "type": nic.get("type"),
                "direct": direct,
                "indirect": indirect,
                "errors": errors,
            }
        )
    return {
        "node": node,
        "site": cfg.site,
        "nics": out,
    }


@dual_mode_tool(
    mcp,
    name="get_logs",
    cli_name="logs",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_logs},
)
@_remediate
async def get_logs(
    switch: SwitchArg,
    lines: Annotated[
        int,
        Field(description="Number of log entries (default 75, max 500)."),
        typer.Option("--lines", "-n", help="Number of entries (max 500)."),
    ] = 75,
    since: Annotated[
        str | None,
        Field(description="Start time filter, e.g. '1h', 'today', '2026-04-21 12:00'."),
    ] = None,
    until: Annotated[
        str | None,
        Field(description="End time filter, e.g. '30 min ago', '2026-04-21 13:00'."),
    ] = None,
    unit: Annotated[
        str | None,
        Field(description="Systemd unit filter, e.g. 'frr.service', 'switchd.service'."),
        typer.Option("--unit", "-u", help="Systemd unit (e.g. frr.service)."),
    ] = None,
    identifier: Annotated[
        str | None,
        Field(description="Syslog identifier filter, e.g. 'bgpd', 'zebra', 'smond'."),
        typer.Option("--identifier", "-t", help="Syslog identifier (e.g. bgpd, zebra)."),
    ] = None,
    priority: Annotated[
        str | None,
        Field(description="Priority filter: name (err, warning) or range (emerg..err)."),
        typer.Option("--priority", "-p", help="Priority or range (err, emerg..err)."),
    ] = None,
    grep: Annotated[
        str | None,
        Field(description="Regex to filter log messages."),
    ] = None,
    boot: Annotated[bool, Field(description="Current boot only.")] = False,
    kernel: Annotated[bool, Field(description="Kernel messages only (dmesg).")] = False,
    preset: Annotated[
        str | None,
        Field(
            description=(
                "Named filter preset: routing, switching, mlag, platform, "
                "nvue, stp, kernel, all-errors. Combinable with explicit filters."
            )
        ),
    ] = None,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Retrieve recent log entries from a switch via journalctl.

    Returns structured log entries (newest first) with timestamp, priority,
    unit, identifier, and message. Use presets for common scenarios like
    ``preset='routing'`` (FRR logs) or ``preset='all-errors'`` (priority >= err).
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    merged = resolve_preset(
        preset,
        unit=unit,
        identifier=identifier,
        priority=priority,
        grep=grep,
        boot=boot,
        kernel=kernel,
    )
    drv = get_driver(cfg, sw)
    entries = await drv.logs(
        lines=lines,
        since=since,
        until=until,
        **merged,
    )
    return {"site": cfg.site, "switch": sw.name, "entries": entries}


@dual_mode_tool(
    mcp,
    name="get_wjh",
    cli_name="wjh",
    annotations={"readOnlyHint": True},
    read_only=True,
    formatters={dict: _fmt_wjh},
)
@_remediate
async def get_wjh(
    switch: SwitchArg,
    limit: Annotated[
        int,
        Field(description="Max packet-drop records to return (default 100). Raise for more."),
    ] = 100,
    site: SiteOpt = None,
) -> dict[str, Any]:
    """Return What Just Happened (WJH) ASIC packet-drop buffer.

    Reports packets dropped by the Spectrum ASIC with the hardware reason
    (ACL deny, L2 lookup fail, TTL expired, buffer overflow, etc.). The buffer
    can be large, so records are capped at ``limit`` (default 100); the response
    reports ``total`` (records before the cap) and ``truncated``.
    """
    cfg, sw = _resolve_switch_or_raise(switch, site)
    drv = get_driver(cfg, sw)
    entries = await drv.wjh()
    total = len(entries)
    capped = entries[:limit] if limit >= 0 else entries
    return {
        "site": cfg.site,
        "switch": sw.name,
        "entries": capped,
        "total": total,
        "truncated": total > len(capped),
    }


def _match_mac(
    cfg: NetworkSiteConfig,
    sw: SwitchEntry,
    entries: list[dict[str, Any]],
    target: str,
) -> list[dict[str, Any]]:
    """Filter MAC-table entries for ``target``, classifying each hit."""
    hits: list[dict[str, Any]] = []
    for entry in entries:
        if _normalize_mac(str(entry.get("mac", ""))) != target:
            continue
        iface = str(entry.get("interface", ""))
        is_direct = iface.startswith("swp")
        hits.append(
            {
                "switch": sw.name,
                "port": iface,
                "vlan": entry.get("vlan"),
                "age": entry.get("age"),
                "direct": is_direct,
                "classification": (_classify_port(cfg, sw, iface) if is_direct else "bond/uplink"),
            }
        )
    return hits


def _normalize_mac(mac: str) -> str:
    """Canonicalize MAC to lowercase colon form."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage::

        uvicorn mcp_network.server:create_app --factory --host 0.0.0.0 --port 8000
    """
    token = (
        settings.mcp_http_access_token.get_secret_value()
        if settings.mcp_http_access_token
        else None
    )
    return create_http_app(
        mcp, path="/mcp", auth_token=token, stateless_http=settings.stateless_http
    )


def main() -> None:
    """CLI entry point: ``mcp-network`` command."""
    log.info("Starting mcp-network v%s (log_level=%s)", __version__, settings.log_level)

    try:
        if settings.transport == "stdio":
            log.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            if settings.mcp_http_access_token:
                mcp.add_middleware(
                    HttpAccessTokenAuth(settings.mcp_http_access_token.get_secret_value())
                )
            log.info("Starting HTTP transport on %s:%s", settings.host, settings.port)
            mcp.run(transport="http", host=settings.host, port=settings.port)
    except Exception as e:
        logging.getLogger(__name__).error("Failed to start MCP server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
