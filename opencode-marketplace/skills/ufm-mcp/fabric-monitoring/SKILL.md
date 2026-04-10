---
name: ufm-fabric-ops
description: Use when investigating InfiniBand fabric issues, checking port health, UFM events, or diagnosing network topology problems. Triggers on mentions of UFM, InfiniBand, fabric, switch ports, or link errors.
---

# UFM Fabric Operations

## Available Tools
- ufm_get_fabric_summary - Overview of fabric health
- ufm_list_switches / ufm_get_switch - Switch inventory and details  
- ufm_list_ports / ufm_get_port - Port status and counters
- ufm_list_events - UFM event log
- ufm_list_links - Link topology

## Common Workflows

### Triage Fabric Issue
1. `ufm_get_fabric_summary()` - Check overall health
2. `ufm_list_events(severity="critical")` - Find recent problems
3. `ufm_list_ports(status="down")` - Identify affected ports

### Investigate Port
1. `ufm_get_port(port_id=ID)` - Get port details and counters
2. Check error counters for link degradation
