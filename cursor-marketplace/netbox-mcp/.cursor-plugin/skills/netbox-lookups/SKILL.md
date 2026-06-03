---
name: netbox-lookup
description: Look up devices, IPs, clusters, sites in NetBox. Triggers on hostnames, IPs, device names, cluster names, site names.
---

# NetBox Lookup

**oob_ip** = BMC/Redfish. **primary_ip** = SSH/apps. For Redfish: ALWAYS use oob_ip.
**provider_machine_id** = vendor/site-operator hostname (NOT manufacturer).

## CLI (`netbox-cli`)

| Command | Example |
|---------|---------|
| lookup-device | `netbox-cli lookup-device "host-01"` |
| lookup-device + site | `netbox-cli lookup-device "host" --site ORI-TX` |
| oob-summary | `netbox-cli oob-summary "host-01"` |
| search (cluster/site) | `netbox-cli search "cartesia5"` |
| devices by cluster | `netbox-cli devices --cluster cartesia5 --fields "id,name,oob_ip"` |
| get-object-by-id | `netbox-cli get-object-by-id dcim.device 1968` |
| list | `netbox-cli list dcim.device --filter "cluster=cartesia5" --fields "id,name,oob_ip"` |
| update-device | `netbox-cli update-device "host" --status offline --confirm` |

Add `--json` for JSON. Run `netbox-cli <cmd> --help` for flags.

**Do not use `lookup`** — that subcommand was removed; Typer will suggest `lookup-device`.
Use `search` for cluster/site names (e.g. `cartesia5`), not `lookup-device`.

## MCP Tools

| Tool | Example |
|------|---------|
| lookup | `netbox_lookup_device(hostname="host-01")` |
| search | `netbox_search_objects(query="q", object_types=["dcim.device"])` |
| get | `netbox_get_object_by_id(object_type="dcim.device", object_id=1968)` |
| list | `netbox_get_objects(object_type="dcim.device", filters={"cluster":"c5"}, fields=["id","name"])` |
| update | `netbox_update_device(device="host", status="offline")` |

Pass `fields` to reduce token usage.

## Data Model

Sites = physical locations. Clusters = cross-site logical groups. Devices belong to a site + optional cluster. Types use dotted notation: `dcim.device`, `ipam.ip_address`.

Device status values: `active`, `planned`, `staged`, `failed`, `inventory`, `decommissioning`, `offline`

Writes require VPN. CLI writes need `--confirm`.
