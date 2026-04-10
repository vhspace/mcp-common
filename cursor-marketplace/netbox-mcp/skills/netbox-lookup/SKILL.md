---
name: netbox-lookup
description: Use when looking up hostnames, devices, IP addresses, rack locations, clusters, or any infrastructure inventory. Triggers on mentions of device names, serial numbers, system identifiers, network addresses, cluster names (e.g. cartesia5), or site names (e.g. ORI-TX, 5C-OH1).
---

# NetBox Lookup

## Choose Your Path

| Path | When to Use |
|------|-------------|
| **CLI** (`netbox-cli`) | Shell access available, token budget matters, compact output |
| **MCP** (`netbox_lookup_device`, etc.) | No shell access, sandboxed agent, need JSON schema validation |

## Critical: OOB vs Primary IP

- **oob_ip** = Out-of-band management (BMC/IPMI) → Use for Redfish
- **primary_ip** = In-band network (OS) → Use for SSH/applications

**For Redfish/BMC: ALWAYS use oob_ip, NEVER primary_ip.**

## CLI Path

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

**Discover flags:** Not all commands support the same options. Run `netbox-cli <command> --help` to see available flags before using them.

Run `netbox-cli --help` for all commands.

| Task | Command |
|------|---------|
| Resolve hostname | `netbox-cli lookup "gpu-node-01"` |
| Search anything | `netbox-cli search "krustykrab"` |
| Get by ID | `netbox-cli get dcim.device 1968` |
| List with filters | `netbox-cli list dcim.device --filter "cluster=cartesia5,status=active"` |
| Fewer fields | `netbox-cli list dcim.device --filter "site=ori-tx" --fields "id,name,oob_ip"` |
| JSON output | `netbox-cli lookup "host" --json` |
| Object types | `netbox-cli types device` |

If not on PATH: `uvx --from netbox-mcp netbox-cli` or `uv run netbox-cli` from the repo.

## MCP Path

| Task | Tool Call |
|------|-----------|
| Resolve hostname | `netbox_lookup_device(hostname="gpu-node-01")` |
| Search anything | `netbox_search_objects(query="krustykrab", object_types=["dcim.device"])` |
| Get by ID | `netbox_get_object_by_id(object_type="dcim.device", object_id=1968)` |
| List with filters | `netbox_get_objects(object_type="dcim.device", filters={"cluster": "cartesia5"}, fields=["id","name","site"])` |
| Change history | `netbox_get_changelogs(filters={"object_type": "dcim.device"})` |

Always pass `fields` to reduce MCP response tokens (~80-90% savings).

## Data Model

- **Sites** = physical locations (ORI-TX, 5C-OH1-H200, IREN-H200)
- **Clusters** = logical groupings spanning sites (cartesia5 spans ORI-TX, 5C-OH1-H200, OCI-IL)
- **Devices** = individual machines, each belonging to a site and optionally a cluster
- **Device Types** = hardware model (H100-80GB-SXM-8x, H200-141GB-SXM)

A cluster name is NOT a site — it's a cross-site grouping. Filter by both cluster AND site when needed.

## Query Strategy

### Unknown name
1. `netbox-cli search "name"` (or `netbox_search_objects`) — searches across all types
2. If empty, try `netbox-cli list dcim.device --filter "cluster=name"` — might be a cluster
3. If still empty, try `netbox-cli list dcim.site --filter "name__ic=name"` — partial site match

### Cluster queries
Always get the site breakdown first — clusters span multiple sites:
```
netbox-cli list dcim.device --filter "cluster=CLUSTER,status=active" --fields "id,name,site,device_type" --limit 5
```

## Cross-Tool Workflow

1. **NetBox** → get device details + oob_ip
2. **Redfish** → `redfish_get_info(host=<oob_ip>)` or `redfish-cli health <oob_ip>`
3. **MAAS/AWX/kubectl** → use hostname or primary_ip as needed

## Key Gotchas

- Read-only — no create/update/delete
- Object types use dotted notation: `dcim.device`, `ipam.ip_address`, `dcim.site`
- `netbox_lookup_device` / `netbox-cli lookup` is preferred for hostname resolution
- Only core NetBox object types supported (no plugin types)
