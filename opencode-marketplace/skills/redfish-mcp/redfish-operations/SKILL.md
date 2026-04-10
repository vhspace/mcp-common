---
name: redfish-bmc-ops
description: Use when managing BMC/IPMI via Redfish, checking BIOS settings, firmware versions, power state, or BMC user accounts. Triggers on mentions of Redfish, BMC, IPMI, BIOS, firmware update, out-of-band management, or server power control.
---

# Redfish BMC Operations

## Credential Setup

Prefer prefixed credentials for multi-site/multi-host use. The resolver supports:
- `<PREFIX>_REDFISH_USER` + `<PREFIX>_REDFISH_PASSWORD`
- `<PREFIX>_REDFISH_LOGIN` + `<PREFIX>_REDFISH_PASSWORD`

Examples:
- `HOST_REDFISH_USER` / `HOST_REDFISH_PASSWORD`
- `ORI_REDFISH_USER` / `ORI_REDFISH_PASSWORD`
- `5C_REDFISH_LOGIN` / `5C_REDFISH_PASSWORD`

Generic fallback (single-credential mode):
- `REDFISH_USER` / `REDFISH_PASSWORD`

## Available Tools

| Tool | Description | Read/Write |
|------|-------------|------------|
| redfish_get_info | System overview (model, serial, power, health) | Read |
| redfish_query | Raw Redfish endpoint query | Read |
| redfish_diff_bios_settings | Compare current vs pending BIOS | Read |
| redfish_check_bios_online | Verify BIOS setting against spec | Read |
| redfish_get_firmware_inventory | List all firmware versions | Read |
| redfish_list_bmc_users | List BMC user accounts | Read |
| redfish_set_nextboot | Set next boot device | Write |
| redfish_set_bios_attributes | Modify BIOS settings | Write |
| redfish_update_firmware | Push firmware update | Write |

## Common Workflows

### Health Check
1. `redfish_get_info(host=OOB_IP)` - Overview
2. `redfish_get_firmware_inventory(host=OOB_IP)` - Firmware versions
3. `redfish_diff_bios_settings(host=OOB_IP)` - Pending changes

### CRITICAL: Use OOB IP
Always use the **oob_ip** from NetBox for Redfish, NOT primary_ip.
- oob_ip = BMC management interface
- primary_ip = OS network interface
