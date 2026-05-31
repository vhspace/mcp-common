---
name: ufm-fabric-ops
description: Use when investigating InfiniBand fabric issues, checking port health, UFM events, or diagnosing network topology problems. Triggers on mentions of UFM, InfiniBand, fabric, switch ports, or link errors.
---

# UFM Fabric Operations

## Real Tools (server.py)

### Health & triage
- `ufm_get_cluster_concerns` — fabric-wide concerns rolled up by host/system
- `ufm_get_concerns` — concerns within a lookback window
- `ufm_list_unhealthy_ports` / `ufm_get_unhealthy_ports_policy`
- `ufm_get_ports_health` — per-port detail (state, BER, FEC counters, peer info, alarms) for one system
- `ufm_check_ports_recent` — `ufm_get_ports_health` + recent log/event slice for the same ports
- `ufm_check_links_recent` — link-state churn for a system in a lookback window
- `ufm_get_high_ber_ports` / `ufm_check_high_ber_recent` — fabric-wide high-BER ports

### Inventory
- `ufm_list_switches` — switch inventory with health summary
- `ufm_list_alarms` — current alarms (GUIDs auto-resolved to hostnames)
- `ufm_list_events` — UFM event log

### Logs
- `ufm_get_log` / `ufm_search_log` / `ufm_search_logs` — direct log queries
- `ufm_create_log_history` / `ufm_create_and_wait_log_history` / `ufm_download_log_history_file`
- `ufm_create_system_dump` / `ufm_create_and_wait_system_dump`
- `ufm_get_job` — poll long-running jobs

### Topaz fabric health (gRPC)
- `ufm_topaz_fabric_health` — overall score
- `ufm_topaz_port_counters` — port error counters
- `ufm_topaz_cables` — cable / transceiver health
- `ufm_topaz_switches` — switch summaries

### Sites & config
- `ufm_list_sites` / `ufm_set_site` / `ufm_get_config` / `ufm_get_version`

### PKey management (write — require explicit intent)
- `ufm_list_pkeys` / `ufm_get_pkey` / `ufm_get_pkey_hosts` / `ufm_pkey_diff`
- `ufm_add_guids_to_pkey` / `ufm_remove_guids_from_pkey`
- `ufm_add_hosts_to_pkey` / `ufm_remove_hosts_from_pkey`

## Common Workflows

### Triage a fabric issue (read-only)
1. `ufm_get_cluster_concerns()` — start here, hosts ranked by recent fabric concern density
2. `ufm_list_alarms()` — currently active alarms with hostnames resolved
3. `ufm_list_events(severity="critical")` — last critical events
4. For a specific host: `ufm_get_ports_health(system="hostname-or-guid")`

### Investigate a port on one system
1. `ufm_get_ports_health(system="sw1", port_numbers=[63])` — full counters & peer info
2. `ufm_check_ports_recent(system="sw1", port_numbers=[63])` — same plus event/log slice
3. Inspect `effective_ber`, `port_fec_uncorrectable_block_counter`, `link_down_counter`, `remote_node_desc`, `remote_guid`

### List all ports on a system
- CLI: `ufm-cli ports SYSTEM_NAME` (omit ports to list all)
- CLI: `ufm-cli ports SYSTEM_NAME --errors-only` (non-Info severity)
- CLI: `ufm-cli ports SYSTEM_NAME --down-only` (physical_state != Active)
- CLI: `ufm-cli ports SYSTEM_NAME --json`
- MCP: `ufm_get_ports_health(system="sw1")` or `ufm_check_ports_recent(system="sw1")`

Output includes: speed, width, FEC mode, effective BER, FEC uncorrectable/correctable counters, symbol errors, link-down count, remote node description with GUID, peer-port summary, and matching active alarms.

### Switches
- CLI: `ufm-cli switches --json`
- CLI: `ufm-cli switches --errors-only`
- MCP: `ufm_list_switches()` or `ufm_list_switches(errors_only=True)`

### Topaz cross-checks (gRPC, per-site)
| Action | CLI |
|---|---|
| Fabric health | `ufm-cli topaz-health --site ori --json` |
| Port counters | `ufm-cli topaz-port-counters --site ori --errors-only --json` |
| Cable health | `ufm-cli topaz-cables --site ori --alarms-only --json` |
| Switch list | `ufm-cli topaz-switches --site ori --json` |

## Design notes
- All listed tools are decorated `@mcp.tool` in `src/ufm_mcp/server.py`. The CI test
  `tests/test_skill_tool_lists.py` enforces that this list cannot drift again.
- All tools accept an optional `site=` parameter when multi-site is configured.
- Write operations (system dumps, log-history downloads, PKey changes) accept a target site
  but otherwise have no `allow_write` gate today; treat them as side-effecting.
