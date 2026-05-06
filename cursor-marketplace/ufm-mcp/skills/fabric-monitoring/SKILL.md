---
name: fabric-monitoring
description: Use when investigating InfiniBand fabric health, BER, port errors, UFM alarms/events, network fabric triage, or searching UFM/SM logs across one or more sites. Triggers on InfiniBand, fabric health, BER, port errors, UFM, network fabric, link issues, unhealthy ports.
---

# Fabric monitoring

Lightweight read-only triage. For the full tool catalog and write operations, see
`skills/ufm-fabric-ops/SKILL.md`.

## Tools used here
- `ufm_get_cluster_concerns` — host-ranked concerns, lookback-aware
- `ufm_get_ports_health` / `ufm_check_ports_recent` — per-port detail on one system
- `ufm_check_links_recent` — link-state churn for a system in a lookback window
- `ufm_get_high_ber_ports` / `ufm_check_high_ber_recent` — fabric-wide high-BER ports
- `ufm_list_unhealthy_ports`
- `ufm_list_alarms` / `ufm_list_events`
- `ufm_search_log` / `ufm_search_logs`
- `ufm_topaz_fabric_health` / `ufm_topaz_port_counters` — gRPC cross-check

## Triage walk

1. **Cluster-level concerns first**
   `ufm_get_cluster_concerns()` — surfaces hosts that are noisy in the fabric right now.

2. **Active alarms**
   `ufm_list_alarms()` — names resolve to hostnames automatically; alarms auto-group by description in CLI when >50% share the same message.

3. **High-BER scan (fabric-wide)**
   `ufm_get_high_ber_ports()` — flags ports whose effective BER exceeds policy thresholds.

4. **Per-host drill-down**
   `ufm_get_ports_health(system="<host-or-switch>")` — counters, FEC, peer-port info, matching alarms.
   Or with recent events: `ufm_check_ports_recent(system=..., lookback_minutes=15)`.

5. **Link churn**
   `ufm_check_links_recent(system=..., lookback_minutes=60)` — for "is the link bouncing?"

6. **Cross-check via Topaz (if a site has it)**
   `ufm_topaz_fabric_health(site=...)` and `ufm_topaz_port_counters(site=...)`.

## Output you can rely on (`ufm_get_ports_health`)
- physical/logical state, active speed/width, FEC mode
- `effective_ber`, `port_fec_uncorrectable_block_counter`, `port_fec_correctable_block_counter`
- `symbol_error_counter`, `link_down_counter`
- `remote_node_desc`, `remote_guid`, `peer_port_dname`, peer-port counter summary
- matching active alarms

## CLI shortcuts
- `ufm-cli concerns` — cluster concerns
- `ufm-cli ports SYSTEM` — port summary, with `--errors-only` / `--down-only` filters
- `ufm-cli alarms` / `ufm-cli events` — current alarms / events
- `ufm-cli ber` — high-BER scan
- `ufm-cli switches --errors-only` — switch health
