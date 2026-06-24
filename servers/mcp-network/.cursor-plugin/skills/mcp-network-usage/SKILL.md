---
name: mcp-network-usage
description: Use when querying network switches — port status, counters, LLDP/BGP neighbors, MAC table, system info, logs, WJH drops — on the ORI-TX NVIDIA Cumulus Linux fabric. Triggers on switch, LLDP, BGP, MAC lookup, fabric, port counters, logs, WJH, journalctl, or hostnames matching *-sw-lea-* / *-sw-spi-*.
---

# mcp-network Usage Guide

`mcp-network` is a **read-only** MCP server for querying Together AI network
switches over SSH. All tools are non-destructive. The first (and currently only)
site is **ORI-TX** — 4 leaf + 2 spine NVIDIA SN5600 switches running Cumulus
Linux 5.15.

## When to use this MCP

- Checking if a switch port is up/down or seeing error counters.
- Finding which switch port a server NIC is cabled to (by MAC or node hostname).
- Viewing LLDP or BGP neighbor state on a switch.
- Getting system info (hostname, uptime, OS version) from a switch.
- Viewing switch journal logs (routing, switchd, kernel, all-errors, etc.).
- Checking What Just Happened (WJH) ASIC packet drops.
- Enumerating sites and switches in the fleet inventory.

## Credentials

SSH credentials are supplied via environment variables named in each site's
inventory JSON. For ORI-TX:

| Env Var | Description |
|---------|-------------|
| `ORI_NETWORK_USER` | Read-only SSH username |
| `ORI_NETWORK_PASSWORD` | Read-only SSH password |

## Available Tools

All 12 tools are read-only. The optional `site` parameter defaults to the
configured default site (ORI-TX).

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_sites` | List all registered sites, the default site, and aliases | — |
| `list_switches` | List switches in a site with role, model, reachability | `site` |
| `get_system_info` | Hostname, OS version, uptime, health, model for one switch | `switch`, `brief` (default True) |
| `get_port_status` | Operational state for one port or all ports, with uplink/downlink classification | `switch`, `port` (optional) |
| `get_port_counters` | Traffic / error / drop / PFC / ECN counters for one port | `switch`, `port` |
| `get_lldp_neighbors` | LLDP neighbor table for one switch | `switch` |
| `get_bgp_neighbors` | BGP neighbor summary (default VRF) | `switch` |
| `get_mac_table` | Bridge MAC-learning table with optional mac/port/vlan filters | `switch`, `mac`, `port`, `vlan` |
| `find_port_for_mac` | Scan all reachable switches for a MAC; distinguishes direct (physical port) from indirect (bond/uplink) hits | `mac` |
| `find_port_for_node` | Resolve a node's NIC MACs via `netbox-cli`, then locate each on the fabric in a single parallel pass | `node` |
| `get_logs` | Retrieve journal log entries with filters and presets | `switch`, `lines`, `since`, `priority`, `preset` |
| `get_wjh` | What Just Happened — ASIC hardware packet drops with drop reason | `switch` |

## Usage Patterns

### Find which port a server is on

```
find_port_for_node(node="research-common-h100-078")
```

This resolves the node's NIC MACs from NetBox, then scans every reachable switch
to find where each NIC's MAC is learned. Direct hits on `swp*` ports indicate
the physical cable connection.

### Check port health

```
get_port_status(switch="dfw01-inb-sw-lea-03", port="swp14s1")
get_port_counters(switch="dfw01-inb-sw-lea-03", port="swp14s1")
```

### Verify BGP/LLDP peering

```
get_bgp_neighbors(switch="dfw01-inb-sw-spi-01")
get_lldp_neighbors(switch="dfw01-inb-sw-lea-01")
```

### View switch logs

```
get_logs(switch="dfw01-inb-sw-lea-01", preset="all-errors")
get_logs(switch="dfw01-inb-sw-lea-01", preset="routing", lines=50)
get_logs(switch="dfw01-inb-sw-lea-01", priority="err", since="1h")
get_logs(switch="dfw01-inb-sw-lea-01", identifier="bgpd", grep="Neighbor.*down")
```

Available presets: `routing`, `switching`, `mlag`, `platform`, `nvue`,
`stp`, `kernel`, `all-errors`. Presets can be combined with explicit filters.

### Check for ASIC hardware drops

```
get_wjh(switch="dfw01-inb-sw-lea-01")
```

## Tips

- Use **netbox-mcp** first to look up expected switch inventory and per-port
  context before querying mcp-network.
- `find_port_for_node` requires `netbox-cli` on PATH.
- Switches are identified by hostname or management IP.
- Spine switches (`*-sw-spi-*`) are leaf uplinks; they won't show direct MAC
  hits for server NICs.
- The `brief=True` default on `get_system_info` returns only key fields to
  save tokens; set `brief=False` for the full raw blob.
