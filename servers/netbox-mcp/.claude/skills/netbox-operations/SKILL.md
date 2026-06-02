---
name: netbox-operations
description: Use when performing infrastructure lookups, device investigations, IP address searches, site audits, or any query that involves NetBox data. Provides structured workflows for common infrastructure operations using NetBox MCP tools.
---

# NetBox Operations

## When to Use

Use this skill when:
- Looking up hostnames, devices, IPs, or infrastructure data
- Investigating a device's configuration, interfaces, or connectivity
- Auditing a site's infrastructure (devices, racks, VLANs, prefixes)
- Searching for recently changed objects or tracking audit trails
- Resolving BMC/management IPs for out-of-band access

## Available Tools

| Tool | Purpose |
|------|---------|
| `netbox_lookup_device` | **Preferred** for hostname/provider machine ID/IP address → device resolution |
| `netbox_update_device` | **Write**: update device status or cluster assignment |
| `netbox_search_objects` | Global search across types -- start here for non-device lookups |
| `netbox_get_objects` | Filtered queries with pagination |
| `netbox_get_object_by_id` | Full details for a specific object |
| `netbox_get_changelogs` | Audit trail: who changed what, when |

## Required Practice: Field Filtering

**Every query MUST include a `fields` parameter.** This reduces token usage by 80-90%.

Common patterns:
- Devices: `fields=["id", "name", "status", "site", "device_type", "primary_ip4", "oob_ip"]`
- IPs: `fields=["id", "address", "status", "dns_name", "assigned_object"]`
- Interfaces: `fields=["id", "name", "type", "enabled", "mac_address"]`
- Sites: `fields=["id", "name", "status", "region"]`

## Workflow: Device Investigation

1. **Lookup** the device by name, provider machine ID, or IP address (searches name first, then Provider_Machine_ID, then IP address via IPAM). Use `site` to disambiguate when provider IDs match across sites:
   ```
   netbox_lookup_device(hostname="<name-or-provider-id>", fields=["id", "name", "status", "site", "primary_ip4", "oob_ip"])
   netbox_lookup_device(hostname="PG22A-6-3-HPC", site="ORI-TX", fields=["id", "name", "oob_ip"])
   ```

2. **Get full details** with the device ID:
   ```
   netbox_get_object_by_id("dcim.device", <id>)
   ```

3. **List interfaces**:
   ```
   netbox_get_objects("dcim.interface", {"device_id": <id>}, fields=["id", "name", "type", "enabled", "mac_address"], limit=50)
   ```

4. **List IP addresses**:
   ```
   netbox_get_objects("ipam.ipaddress", {"device_id": <id>}, fields=["id", "address", "status", "dns_name"])
   ```

## Workflow: Site Audit

1. **Find the site**: `netbox_get_objects("dcim.site", {"name__ic": "<site>"}, fields=["id", "name", "status"])`
2. **List devices**: `netbox_get_objects("dcim.device", {"site_id": <id>}, fields=["id", "name", "status", "device_type"], limit=100)`
3. **List racks**: `netbox_get_objects("dcim.rack", {"site_id": <id>}, fields=["id", "name", "u_height"])`
4. **List prefixes**: `netbox_get_objects("ipam.prefix", {"site_id": <id>}, fields=["id", "prefix", "status"])`

## Workflow: BMC/Redfish Access

**Critical**: Use `oob_ip` (out-of-band management IP) for Redfish, NOT `primary_ip`.

1. Lookup device: `netbox_lookup_device(hostname="<hostname-or-provider-id>", fields=["id", "name", "oob_ip", "primary_ip4"])`
2. Extract `oob_ip_address` from the result for BMC/Redfish operations (convenience field, already stripped of CIDR).

## Cross-Relationship Queries

Multi-hop filters are not supported. Use two-step queries:
```
# Find site first
sites = netbox_get_objects("dcim.site", {"name": "NYC"}, fields=["id"])
# Then query devices at that site
devices = netbox_get_objects("dcim.device", {"site_id": sites["results"][0]["id"]}, ...)
```

## Workflow: Update Device Status

**Write operation** — requires VPN connectivity. The MCP tool has `destructiveHint: true`.

1. **Update status**:
   ```
   netbox_update_device(device="gpu-node-01", status="offline")
   ```

2. **Update cluster assignment**:
   ```
   netbox_update_device(device="gpu-node-01", cluster="newcluster")
   ```

3. **Update both**:
   ```
   netbox_update_device(device="gpu-node-01", status="active", cluster="cartesia5")
   ```

Valid status values: `active`, `planned`, `staged`, `failed`, `inventory`, `decommissioning`, `offline`

The device can be specified by hostname (case-insensitive partial match) or numeric ID.
Returns the updated device record and a summary of changes (old → new).

## Pagination

MCP default limit is 5. CLI aliases (`devices`, `sites`, `clusters`, `ips`) default to 50. Check `count` in responses to know total results. Increase limit or use `offset` for large datasets.
