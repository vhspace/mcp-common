---
name: ufm-triage
description: Use when performing operational triage on UFM-managed InfiniBand clusters. Provides a structured workflow for investigating alarms, events, high BER ports, and log errors.
---

# UFM Cluster Triage

## When to Use

- Investigating InfiniBand fabric issues
- Responding to UFM alarms or events
- Checking cluster health after maintenance
- Investigating high bit-error-rate (BER) ports

## Triage Protocol

### 1. Get the Overview

Call `ufm_list_switches` or `ufm-cli switches --json` for a quick switch inventory.
Call `ufm_get_cluster_concerns` to get a one-shot summary:
- Alarm severity breakdown and top alarm types
- Recent non-info events
- Log error summaries (UFM + SM logs)
- High BER port counts
- Link severity distribution

### 2. Investigate High BER

If high BER ports are reported:
- Note the severity counts (Warning vs Critical)
- Check `top_ports_by_recent_events` for the most active problem ports
- Use `ufm_check_high_ber_recent` for deeper analysis if needed

### 3. Drill Into Specific Ports

For specific problem ports:
- Call `ufm_check_ports_recent` with system name and port numbers
- Omit port_numbers to list all ports on a system
- Use `errors_only=True` or `down_only=True` to filter large results
- Review physical_state, logical_state, severity
- Check FEC counters: fec_uncorrectable (non-zero = bad), fec_correctable, effective_ber
- Check symbol_error_counter and link_down_counter
- Identify remote end: remote_node_desc, remote_guid
- Check peer port state (the other end of the link)
- Look at matching alarms and recent log entries

### 4. Search Logs

For specific error patterns:
- Use `ufm_search_logs` with keywords like error codes or system names
- Default searches UFM + SM logs
- Use regex=true for complex patterns

### 5. Report Findings

Summarize:
- Total alarms by severity
- Top recurring alarm/event types
- Affected systems and ports
- Whether issues are active or historical
- Recommended actions (cable replacement, port disable, etc.)

## Topaz Fabric Health (gRPC)

| Action | CLI |
|---|---|
| Fabric health | `ufm-cli topaz-health --site ori --json` |
| Port counters | `ufm-cli topaz-port-counters --site ori --errors-only --json` |
| Cable health | `ufm-cli topaz-cables --site ori --alarms-only --json` |
| Switch list | `ufm-cli topaz-switches --site ori --json` |

| Tool | Description |
|---|---|
| ufm_topaz_fabric_health | Overall fabric health score |
| ufm_topaz_port_counters | Port error counters |
| ufm_topaz_cables | Cable/transceiver health |
| ufm_topaz_switches | Switch summaries |

## Multi-Site

If multiple UFM sites are configured:
- Call `ufm_list_sites` first to see available sites
- Pass `site=` to each tool call to target the right cluster
- Compare across sites if investigating a widespread issue
