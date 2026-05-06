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

Credential priority (highest to lowest):
1. CLI flags: `--user` / `--password`
2. Explicit env vars: `REDFISH_USER` / `REDFISH_PASSWORD`
3. Vendor auto-detection: site-specific credentials matched by IP range

## Available Tools

| Tool | Description | Read/Write |
|------|-------------|------------|
| redfish_get_info | System overview (model, serial, power, health) | Read |
| redfish_query | Raw Redfish endpoint query | Read |
| redfish_diff_bios_settings | Compare current vs pending BIOS | Read |
| redfish_check_bios_online | Verify BIOS setting against spec | Read |
| redfish_get_firmware_inventory | List all firmware versions | Read |
| redfish_list_bmc_users | List BMC user accounts | Read |
| redfish_get_bmc_logs | Read BMC logs (auto-discovers services) | Read |
| redfish_get_fixed_boot_order | Supermicro persistent UEFI boot order | Read |
| redfish_set_nextboot | Set next boot device | Write |
| redfish_set_fixed_boot_order | Set Supermicro persistent boot order | Write |
| redfish_set_bios_attributes | Modify BIOS settings | Write |
| redfish_update_firmware | Push firmware update | Write |

## Common Workflows

### Health Check
1. `redfish_get_info(host=OOB_IP)` - Overview
2. `redfish_get_firmware_inventory(host=OOB_IP)` - Firmware versions
3. `redfish_diff_bios_settings(host=OOB_IP)` - Pending changes

### NVIDIA HGX/DGX GPU Baseboard
The hardware_db includes an NVIDIA HGX-Baseboard entry for GPU tray BMCs
(e.g. Dell XE9780 with HGX B300). These BMCs report `Model="NA"` via Redfish
and are auto-detected by manufacturer + model match. GPU tray BMCs have no
BIOS settings — use them for GPU health, power, SEL logs, and firmware only.

### BMC Screenshots & Screen Analysis

Capture the VGA console framebuffer from a BMC and optionally run LLM-powered
analysis on the image.

```
redfish-cli screenshot OOB_IP                          # save screenshot.jpg
redfish-cli screenshot OOB_IP --analyze summary        # quick screen summary
redfish-cli screenshot OOB_IP --analyze analysis       # detailed analysis
redfish-cli screenshot OOB_IP --analyze diagnosis      # full diagnosis
redfish-cli screenshot OOB_IP --analyze diagnosis --analysis-timeout 300
redfish-cli screenshot-by-name HOSTNAME --analyze summary
```

Each `--analyze` mode has a per-mode default timeout for the Together API call:

| Mode | Default timeout |
|------|----------------|
| `summary` | 90 s |
| `analysis` | 120 s |
| `diagnosis` | 180 s |

Override with `--analysis-timeout SECONDS` when analyzing large or complex
screens that need more time.

### CRITICAL: Use OOB IP
Always use the **oob_ip** from NetBox for Redfish, NOT primary_ip.
- oob_ip = BMC management interface
- primary_ip = OS network interface
