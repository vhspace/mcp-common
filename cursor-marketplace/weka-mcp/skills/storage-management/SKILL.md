---
name: storage-management
description: Use when investigating Weka distributed storage health, filesystem capacity, storage cluster status, S3 storage operations, container monitoring, or drive health on Weka clusters. Triggers on Weka storage, filesystem capacity, storage cluster, S3 storage, distributed storage, snapshot management.
---

# Weka Storage Management

Covers **Weka 4.4.x** (REST API v2, port 14000). Always check Weka when investigating storage capacity, filesystem health, or S3 bucket status.

## Choose Your Path

This plugin provides two interfaces. Prefer CLI when shell access is available — it uses ~90% fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`weka-cli`) | Agent has shell access, token budget matters, compact output preferred |
| **MCP** (`weka_cluster_overview`, etc.) | No shell access, sandboxed agent, need structured JSON schema validation |

## Quick Start (triage)

**CLI:**
```
weka-cli health                    # cluster status + active alerts + license
weka-cli alerts --severity CRITICAL  # drill into critical alerts
weka-cli filesystems               # filesystem capacity
weka-cli events --severity ERROR --limit 20  # recent errors
```

**MCP:**
```
weka_cluster_overview()
weka_list(resource="alerts", filters={"severity": "CRITICAL"})
weka_list(resource="filesystems")
weka_get_events(severity="ERROR", num_results=20)
```

## Known Normal Behavior

These patterns are **expected** with org-scoped credentials — do NOT remediate.

| Signal | Explanation |
|--------|-------------|
| `0/N running (N UNKNOWN)` in `weka local status` | Org user cannot query backend node status (permission-gated to cluster-admin). Client container is fine. |
| "SIGTERM signal received (X ago)" on all slots | Historical timestamp of last node restart, not an active error. |

**Verify client is healthy:** (1) `weka local status` shows container line as `RUNNING`, (2) `weka status` returns cluster info without errors, (3) `kubectl get pod -n weka -l app=weka-storage-node` shows Running. All pass → healthy, do not restart.

## CLI Path

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

**Discover flags:** Not all commands support the same options. Run `weka-cli <command> --help` to see available flags before using them.

Run `weka-cli --help` for all commands.

| Task | Command |
|------|---------|
| Cluster health | `weka-cli health` |
| List filesystems | `weka-cli filesystems` |
| List containers | `weka-cli containers` |
| List servers/nodes | `weka-cli nodes` |
| List drives | `weka-cli drives` |
| Active alerts | `weka-cli alerts` |
| Critical alerts only | `weka-cli alerts --severity CRITICAL` |
| Recent events | `weka-cli events --limit 20` |
| Error events | `weka-cli events --severity ERROR` |
| Performance stats | `weka-cli stats` |
| Real-time stats | `weka-cli stats --realtime` |
| List snapshots | `weka-cli snapshots` |
| Snapshots for a FS | `weka-cli snapshots --fs <uid>` |
| List processes | `weka-cli processes` |
| S3 buckets | `weka-cli s3 buckets` |
| S3 cluster status | `weka-cli s3 status` |
| Generic list | `weka-cli list <resource_type>` |
| Get by UID | `weka-cli get <resource_type> <uid>` |
| JSON output | `weka-cli health --json` |

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

All tools support a `fields` parameter for response projection to reduce token usage.

## Resource Types for `weka_list` / `weka-cli list`

19 types: `alerts`, `alert_types`, `alert_descriptions`, `containers`, `drives`, `events`, `failure_domains`, `filesystem_groups`, `filesystems`, `interface_groups`, `organizations`, `processes`, `s3_buckets`, `servers`, `smb_shares`, `snapshot_policies`, `snapshots`, `tasks`, `users`.

## Configuration

Required env vars:
- `WEKA_HOST` — Weka cluster URL (e.g. `https://weka01:14000`)
- `WEKA_PASSWORD` — Weka API password

Optional:
- `WEKA_USERNAME` (default: `admin`)
- `API_BASE_PATH` (default: `/api/v2`)
- `VERIFY_SSL` (default: `true`)
- `TIMEOUT_SECONDS` (default: `30`)

Converged clusters run Weka processes alongside compute — check process health if GPU workloads degrade.

## Converged vs Hosted

- **Converged** (storage co-located with GPU nodes): focus on `containers`, `drives`, `processes`, `failure_domains`, stats — storage health directly impacts GPU workloads.
- **Hosted** (dedicated storage cluster): focus on `filesystems`, `s3_buckets`, `smb_shares`, `interface_groups`, `organizations` — protocol health and multi-tenant isolation.

## CRITICAL: Client Setup on Hosted Clusters

> **DESTRUCTIVE if done wrong.** On hosted clusters, GPU nodes are clients only — they MUST NOT join as storage backends.

**Safe command** (frontend-only client):
```
weka local setup client --name default --net bond0 --cores 8 --join-ips 192.168.231.211,192.168.231.212,192.168.231.213
```

| Safe | NEVER use on hosted clusters |
|------|------------------------------|
| `weka local setup client --name <n> --net <iface> --cores <N> --join-ips <ips>` | `weka cluster container` (adds GPU node as storage backend, **corrupts the hosted cluster**) |

`weka local setup client` creates a frontend-only client container. `weka cluster container` adds a node as a backend member of the storage cluster. On a hosted cluster (where storage runs on dedicated appliances, not GPU nodes), using `weka cluster container` is destructive and hard to reverse.

## Cross-MCP Integration

- **NetBox MCP** — container hostnames from `weka-cli containers` / `weka_list(resource="containers")` map to NetBox device records for rack/site info
- **AWX MCP** — trigger remediation playbooks for drive failures or node decommissioning
- **Redfish MCP** — check BMC health on converged Weka nodes (use NetBox for OOB IP)
- **UFM MCP** — Weka runs over InfiniBand; correlate storage errors with IB fabric health
