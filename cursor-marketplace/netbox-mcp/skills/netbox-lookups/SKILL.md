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
| lookup | `netbox-cli lookup "host-01"` |
| lookup + site | `netbox-cli lookup "host" --site ORI-TX` |
| search | `netbox-cli search "query"` |
| get | `netbox-cli get dcim.device 1968` |
| list | `netbox-cli list dcim.device --filter "cluster=cartesia5" --fields "id,name,oob_ip"` |
| update | `netbox-cli update-device "host" --status offline --confirm` |

Add `--json` for JSON. Run `netbox-cli <cmd> --help` for flags.

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

Writes require VPN. CLI writes need `--confirm`.
