---
name: netbox-maas-cross-reference
description: Cross-reference devices between NetBox and MAAS when node hostnames differ from MAAS machine names. Use when looking up a node in MAAS, triaging PagerDuty incidents involving specific nodes, or when a MAAS search by node hostname returns no results. Triggers on MAAS 404s for node names, PagerDuty GPU alerts, or any workflow requiring both NetBox device data and MAAS machine data.
---

# NetBox ↔ MAAS Cross-Reference

## The Problem

NetBox and MAAS use **different names** for the same physical machine:

| System | Name | Example |
|--------|------|---------|
| **NetBox** | Logical cluster node name | `caw1b-b200-2-038` |
| **MAAS** | Vendor/datacenter machine name | `PG38A-10-2-HPC` |
| **PagerDuty / K8s** | FQDN of logical name | `caw1b-b200-2-038.cloud.together.ai` |

Searching MAAS by the NetBox/K8s hostname will return **404 / empty**.

## Shortcut: Starting from a MAAS/Provider Name

If you already have the MAAS/datacenter machine name (e.g. from a rack label, MAAS event, or PagerDuty alert), resolve it directly:

```bash
netbox-cli lookup "PG38A-10-2-HPC"
```

Or via MCP:
```
netbox_lookup_device(hostname="PG38A-10-2-HPC")
```

The lookup command searches by device name first, then falls back to Provider_Machine_ID automatically. Use `--site` to disambiguate when multiple devices share similar IDs.

## Lookup Workflow (NetBox → MAAS)

### 1. Start from NetBox — get the Provider Machine ID

```bash
netbox-cli lookup "caw1b-b200-2-038" --json
```

Or via MCP:

```
netbox_search_objects(query="caw1b-b200-2-038", object_types=["dcim.device"])
```

Extract from the result:
- `custom_fields.Provider_Machine_ID` → MAAS hostname (e.g. `PG38A-10-2-HPC`)
- `site.slug` → maps to MAAS zone (e.g. `iren-b200-3` → zone `ca-west-1b`)
- `oob_ip` → BMC/Redfish IP

### 2. Search MAAS by Provider Machine ID

```
maas_list_machines(filters={"hostname": "<Provider_Machine_ID>"}, fields=["system_id", "hostname", "status_name", "power_state", "zone"])
```

### 3. Use MAAS system_id for further operations

Once you have the MAAS `system_id`, use it for:
- `maas_get_machine(system_id=...)` — full details + power parameters
- `maas_check_bmc_health(system_id=...)` — BMC credential verification
- `maas_list_events(system_id=...)` — machine event history

## Site → MAAS Instance Mapping

| NetBox Site Pattern | MAAS Instance | Zone Pattern |
|---------------------|---------------|--------------|
| ORI-* | `ori` | Default zone |
| IREN-*, APLD-*, 5C-* | `default` (central) | Site-specific zones |

## Key Fields in NetBox for MAAS Cross-Reference

| NetBox Field | Purpose |
|--------------|---------|
| `custom_fields.Provider_Machine_ID` | MAAS hostname |
| `site.slug` | Determines MAAS instance and zone |
| `serial` | Alternative MAAS lookup key |
| `oob_ip` | BMC IP for Redfish (not in MAAS) |

## Common Mistake

```
# WRONG — NetBox logical name, will 404 in MAAS
maas_get_machine(system_id="caw1b-b200-2-038")

# RIGHT — use Provider_Machine_ID from NetBox
maas_list_machines(filters={"hostname": "PG38A-10-2-HPC"})
# → returns system_id "hbhwb8"
maas_get_machine(system_id="hbhwb8")
```
