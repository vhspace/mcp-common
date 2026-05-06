---
name: redfish-mcp
description: Use when performing Redfish/BMC operations, hardware management, BIOS configuration, firmware updates, or server health checks via the redfish-mcp MCP server
---

# Redfish MCP Skill

## Decision Tree

| Need | Tool |
|------|------|
| System info (model, health, boot, drives) | `redfish_get_info` with appropriate `info_types` |
| Specific BIOS/boot/power/health value | `redfish_query` (targeted, less data) |
| Compare two hosts' BIOS | `redfish_diff_bios_settings` (use `smart_match=True`) |
| All firmware versions | `redfish_get_firmware_inventory` |
| Hardware docs, specs, known issues | `redfish_get_hardware_docs` (cached 24h) |
| Security advisories / CVEs | `redfish_get_vendor_errata` |
| Latest BIOS from vendor website | `redfish_check_bios_online` |
| Change BIOS settings | Check current first → `redfish_set_bios_attributes` |
| Change boot target | `redfish_set_nextboot` |
| Supermicro persistent boot order | `redfish_get_fixed_boot_order` / `redfish_set_fixed_boot_order` |
| BMC user accounts | `redfish_list_bmc_users` |
| Store a finding for later | `redfish_agent_report_observation` |

## Credential & Host Resolution Flow

1. Look up the host in **NetBox MCP** (`netbox_search_objects`)
2. Use `oob_ip` (NOT `primary_ip`) for Redfish access
3. Credentials are elicited automatically if not provided
4. Credentials are cached per-session, per-host

## Best Practices

- **Read before write** — always check current state before changing BIOS/boot
- **Prefer `redfish_query`** over `redfish_get_info` with `bios_current` for single values
- **Use `execution_mode="render_curl"`** when the user wants manual commands
- **Write tools require `allow_write=True`** — omitting it will error
- **BIOS changes need reboot** to take effect (`reboot=True` or manual)
- **Store findings** with `redfish_agent_report_observation` for cross-session reuse
- **Check hardware docs first** (`redfish_get_hardware_docs`) before firmware updates
- BMCs are fragile: 1 concurrent request per host (enforced automatically)

## Common Patterns

**Check a specific BIOS attribute (e.g., Resizable BAR):**
```
1. redfish_query(host=X, query_type="bios_attribute", key="Re_SizeBARSupport_00B2")
2. If not found, try key="Re_SizeBARSupport" (naming varies by firmware)
```

**Compare fleet host to baseline:**
```
1. redfish_diff_bios_settings(host_a=baseline, host_b=target, smart_match=True)
2. Review critical_differences in response
```

**GPU-optimized BIOS setup:**
```
1. redfish_get_hardware_docs(host=X) → check recommended_settings
2. redfish_get_info(host=X, info_types=["bios_current"]) → verify current
3. redfish_set_bios_attributes(host=X, attributes={...}, allow_write=True, reboot=True)
```

**BMC screenshot with analysis (CLI only):**
```
redfish-cli screenshot OOB_IP --analyze summary          # quick summary (90s timeout)
redfish-cli screenshot OOB_IP --analyze analysis         # detailed (120s timeout)
redfish-cli screenshot OOB_IP --analyze diagnosis        # full diagnosis (180s timeout)
redfish-cli screenshot OOB_IP --analyze diagnosis --analysis-timeout 300
redfish-cli screenshot-by-name HOSTNAME --analyze summary
```

Per-mode default timeouts: summary=90s, analysis=120s, diagnosis=180s.
Override with `--analysis-timeout SECONDS` for complex screens.
