---
name: storage-management
description: Use when investigating Weka distributed storage health, filesystem capacity, storage cluster status, S3 storage operations, container monitoring, or drive health on Weka clusters. Triggers on Weka storage, filesystem capacity, storage cluster, S3 storage, distributed storage, snapshot management.
---

# Weka Storage Management

Weka is the distributed storage layer. Always check Weka when investigating storage capacity, filesystem health, or S3 bucket status.

## Choose Your Path

This plugin provides two interfaces. Prefer CLI when shell access is available — it uses ~90% fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`weka-cli`) | Agent has shell access, token budget matters, compact output preferred |
| **MCP** (`weka_cluster_overview`, etc.) | No shell access, sandboxed agent, need structured JSON schema validation |

## Quick Start (triage)

**CLI:**
```
weka-cli sites                       # discover configured sites
weka-cli health --site ori           # cluster status + alerts + license for ORI site
weka-cli alerts --severity CRITICAL --site ori
weka-cli filesystems --site ori      # filesystem capacity
weka-cli events --severity ERROR --limit 20 --site ori
```

**MCP:**
```
weka_list_sites()
weka_cluster_overview(site="ori")
weka_list(resource="alerts", filters={"severity": "CRITICAL"}, site="ori")
weka_list(resource="filesystems", site="ori")
weka_get_events(severity="ERROR", num_results=20, site="ori")
```

## Configuration

### Multi-site (production)

The workspace runs multiple Weka clusters. Sites are discovered from environment variables matching `WEKA_{SITE}_URL`:

| Variable | Description | Example |
|----------|-------------|---------|
| `WEKA_{SITE}_URL` | Weka cluster URL | `WEKA_ORI_URL=https://weka01.ori:14000` |
| `WEKA_{SITE}_ADMIN` | Admin username | `WEKA_ORI_ADMIN=admin` |
| `WEKA_{SITE}_ADMIN_PASSWORD` | Admin password | `WEKA_ORI_ADMIN_PASSWORD=secret` |
| `WEKA_{SITE}_ORG` | Organization scope (optional) | `WEKA_ORI_ORG=root` |
| `WEKA_{SITE}_VERIFY_SSL` | SSL verification (optional) | `WEKA_ORI_VERIFY_SSL=false` |

`{SITE}` is uppercase: `ORI`, `DFW01`, `OH1`, etc. The SiteManager auto-discovers all `WEKA_*_URL` variables at startup.

Run `weka-cli sites` to see which sites are configured and which is active.

**Alternative credential key:** `WEKA_{SITE}_USERNAME` / `WEKA_{SITE}_PASSWORD` also work (the `_ADMIN` / `_ADMIN_PASSWORD` variants take precedence).

### Single-site (simple)

For a single cluster, use the base env vars (these become the "default" site):

- `WEKA_HOST` — Weka cluster URL (e.g. `https://weka01:14000`)
- `WEKA_PASSWORD` — Weka API password
- `WEKA_USERNAME` (default: `admin`)
- `API_BASE_PATH` (default: `/api/v2`)
- `VERIFY_SSL` (default: `true`)
- `TIMEOUT_SECONDS` (default: `30`)

### Site aliases

Set `WEKA_SITE_ALIASES_JSON='{"texas": "ori", "ohio": "oh1"}'` to create friendly names.

Set `WEKA_DEFAULT_SITE=ori` to change which site is used when `--site` is omitted.

## CLI Path

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

**Discover flags:** Not all commands support the same options. Run `weka-cli <command> --help` to see available flags before using them.

Run `weka-cli --help` for all commands. All commands accept `--site <name>` (`-s`).

| Task | Command |
|------|---------|
| List configured sites | `weka-cli sites` |
| Cluster health | `weka-cli health --site ori` |
| List filesystems | `weka-cli filesystems --site ori` |
| Capacity summary | `weka-cli capacity --site ori` |
| List containers | `weka-cli containers --site ori` |
| List servers/nodes | `weka-cli nodes --site ori` |
| List drives | `weka-cli drives --site ori` |
| Active alerts | `weka-cli alerts --site ori` |
| Critical alerts only | `weka-cli alerts --severity CRITICAL --site ori` |
| Recent events | `weka-cli events --limit 20 --site ori` |
| Error events | `weka-cli events --severity ERROR --site ori` |
| Performance stats | `weka-cli stats --site ori` |
| Real-time stats | `weka-cli stats --realtime --site ori` |
| List snapshots | `weka-cli snapshots --site ori` |
| Snapshots for a FS | `weka-cli snapshots --fs <uid> --site ori` |
| List organizations | `weka-cli orgs --site ori` |
| List users | `weka-cli users --site ori` |
| List processes | `weka-cli processes --site ori` |
| S3 buckets | `weka-cli s3 buckets --site ori` |
| S3 cluster status | `weka-cli s3 status --site ori` |
| Generic list | `weka-cli list <resource_type> --site ori` |
| Get by UID | `weka-cli get <resource_type> <uid> --site ori` |
| JSON output | `weka-cli health --json --site ori` |

If `weka-cli` is not on PATH, install with `uvx --from weka-mcp weka-cli` or run from the repo with `uv run weka-cli`.

## MCP Path

### Read Tools (6)
| Tool | Description |
|------|-------------|
| `weka_cluster_overview` | One-shot: cluster status + active MAJOR/CRITICAL alerts + license |
| `weka_list` | List any of 19 resource types (containers, drives, filesystems, snapshots, etc.) |
| `weka_get` | Get a single resource by UID (11 types) |
| `weka_get_events` | Query event log with severity/category/time filters |
| `weka_get_stats` | Cluster performance stats (historical or realtime) |
| `weka_list_quotas` | Directory quotas for a filesystem |

### Write Tools (7)
| Tool | Risk | Description |
|------|------|-------------|
| `weka_manage_alert` | Low | Mute/unmute alert types during maintenance |
| `weka_create_filesystem` | Medium | Create new filesystem with capacity and optional tiering |
| `weka_create_snapshot` | Low | Create point-in-time snapshot (read-only or writable) |
| `weka_upload_snapshot` | Low | Upload snapshot to object storage for DR |
| `weka_restore_filesystem` | Medium | Restore filesystem from object-store snapshot |
| `weka_manage_s3` | Medium-High | Create/update/delete S3 cluster |
| `weka_delete_resource` | **Destructive** | Delete filesystems, snapshots, or S3 cluster |

All tools support a `site` parameter and a `fields` parameter for response projection to reduce token usage.

## Resource Types for `weka_list` / `weka-cli list`

19 types: `alerts`, `alert_types`, `alert_descriptions`, `containers`, `drives`, `events`, `failure_domains`, `filesystem_groups`, `filesystems`, `interface_groups`, `organizations`, `processes`, `s3_buckets`, `servers`, `smb_shares`, `snapshot_policies`, `snapshots`, `tasks`, `users`.

## Converged vs Hosted

- **Converged** (storage co-located with GPU nodes): focus on `containers`, `drives`, `processes`, `failure_domains`, stats — storage health directly impacts GPU workloads.
- **Hosted** (dedicated storage cluster): focus on `filesystems`, `s3_buckets`, `smb_shares`, `interface_groups`, `organizations` — protocol health and multi-tenant isolation.

Weka 4.4.x specific: REST API v2 on port 14000. Converged clusters run Weka processes alongside compute — check process health if GPU workloads degrade.

## Cross-MCP Integration

- **NetBox MCP** — container hostnames from `weka-cli containers` / `weka_list(resource="containers")` map to NetBox device records for rack/site info
- **AWX MCP** — trigger remediation playbooks for drive failures or node decommissioning
- **Redfish MCP** — check BMC health on converged Weka nodes (use NetBox for OOB IP)
- **UFM MCP** — Weka runs over InfiniBand; correlate storage errors with IB fabric health
