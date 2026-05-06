# ufm-mcp

[![Release](.github/badges/release.svg)](https://github.com/vhspace/ufm-mcp/releases)
[![CI](https://github.com/vhspace/ufm-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/ufm-mcp/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://github.com/vhspace/ufm-mcp/blob/main/LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

MCP server for NVIDIA UFM (Unified Fabric Manager). Provides operational tools for InfiniBand fabric monitoring, triage, and log analysis across one or more UFM sites.

## Quick Start Triage

```
1. ufm_get_cluster_concerns  → one-call summary of alarms, events, logs, BER, links
2. ufm_check_high_ber_recent → drill into high bit-error-rate ports
3. ufm_check_ports_recent    → investigate specific ports with logs + events
4. ufm_search_logs           → keyword search across UFM/SM logs
```

## Install

### Cursor (one-click)

[![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](cursor://anysphere.cursor-deeplink/mcp/install?name=ufm-mcp&config=eyJjb21tYW5kIjoidXYiLCJhcmdzIjpbIi0tZGlyZWN0b3J5IiwiL3BhdGgvdG8vdWZtLW1jcCIsInJ1biIsInVmbS1tY3AiXSwiZW52Ijp7IlVGTV9VUkwiOiJodHRwczovL1lPVVJfVUZNX0hPU1QvIiwiVUZNX1RPS0VOIjoiWU9VUl9UT0tFTiIsIlZFUklGWV9TU0wiOiJmYWxzZSJ9fQ==)

> After install, edit the env vars in `.cursor/mcp.json` with your UFM_URL and UFM_TOKEN.

### Claude Code

```bash
claude mcp add ufm-mcp \
  -e UFM_URL=https://YOUR_UFM_HOST/ \
  -e UFM_TOKEN=YOUR_TOKEN \
  -e VERIFY_SSL=false \
  -- uv --directory /path/to/ufm-mcp run ufm-mcp
```

### Universal (any agent)

```bash
npx add-mcp "uv --directory /path/to/ufm-mcp run ufm-mcp" --name ufm-mcp
```

### Manual

Add to `.cursor/mcp.json` (Cursor) or `.mcp.json` (Claude Code):

```json
{
  "mcpServers": {
    "ufm-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/ufm-mcp", "run", "ufm-mcp"],
      "env": {
        "UFM_URL": "https://172.19.2.60/",
        "UFM_TOKEN": "YOUR_TOKEN",
        "VERIFY_SSL": "false"
      }
    }
  }
}
```

## Tools

### Triage (start here)

| Tool | Description |
|------|-------------|
| `ufm_get_cluster_concerns` | **One-call triage**: alarms + events + logs + high-BER + links in a single summary |
| `ufm_get_concerns` | Filtered summary of active warnings/errors/critical concerns |

### Port & Link Health

| Tool | Description |
|------|-------------|
| `ufm_get_high_ber_ports` | List ports with high BER (Bit Error Rate) — key IB health metric |
| `ufm_check_high_ber_recent` | High-BER summary correlated with recent alarm/event activity |
| `ufm_get_ports_health` | Detailed port health: physical/logical state, speed, BER, peer info |
| `ufm_check_ports_recent` | Port health + recent logs/events in one call |
| `ufm_check_links_recent` | Link severity summary + recent link-related alarms/events |
| `ufm_list_unhealthy_ports` | Ports isolated by UFM due to persistent errors |
| `ufm_get_unhealthy_ports_policy` | Policy rules UFM uses to flag unhealthy ports |

### Alarms & Events

| Tool | Description |
|------|-------------|
| `ufm_list_alarms` | List/fetch active alarms (persistent conditions until cleared) |
| `ufm_list_events` | List/fetch events (point-in-time occurrences with timestamps) |

### Logs

| Tool | Description |
|------|-------------|
| `ufm_get_log` | Download UFM/SM/Event log text |
| `ufm_search_log` | Search within a single log type |
| `ufm_search_logs` | Search across multiple log types (supports regex) |

### Write Operations

| Tool | Description |
|------|-------------|
| `ufm_create_log_history` | Create server-side log history file (`allow_write=true` required) |
| `ufm_download_log_history_file` | Download a completed history file |
| `ufm_create_system_dump` | Trigger UFM system dump (`allow_write=true` required) |
| `ufm_get_job` | Poll job status for async operations |

### Site Management

| Tool | Description |
|------|-------------|
| `ufm_list_sites` | List configured UFM sites and aliases |
| `ufm_set_site` | Set active site for subsequent calls |
| `ufm_get_config` | Effective configuration (secrets redacted) |
| `ufm_get_version` | UFM version info |

## InfiniBand Glossary

| Term | Meaning |
|------|---------|
| **BER** | Bit Error Rate — fraction of corrupted bits on a link |
| **SM** | Subnet Manager — manages IB fabric routing and topology |
| **GUID** | Globally Unique Identifier — hex ID for switches/HCAs |
| **HCA** | Host Channel Adapter — IB network interface on a server |
| **dname** | Display name — human-readable port identifier |

## Configuration

Required:
- `UFM_URL` — Base URL of UFM (e.g., `https://172.19.2.60/`)

Recommended:
- `UFM_TOKEN` — Access token (sent as `Authorization: Basic <token>`)

Optional:
- `VERIFY_SSL` — `true` (default) or `false`
- `TIMEOUT_SECONDS` — default `30`
- `LOG_LEVEL` — `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`
- `TRANSPORT` — `stdio` (default) or `http`
- `MCP_HTTP_ACCESS_TOKEN` — required if `TRANSPORT=http`

### Multi-site

Configure multiple UFM instances in a single MCP process:

- `UFM_DEFAULT_SITE` — active site at startup (default: `"default"`)
- `UFM_<SITE>_URL` / `UFM_<SITE>_TOKEN` — per-site URL and token
- `UFM_SITE_ALIASES_JSON` — alias map, e.g. `{"oh1":"5c_oh1","md1":"5c_md1"}`

## Development

```bash
uv sync --group dev
uv run pytest -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/
```

## Prompts

The server includes MCP prompts for common workflows:

- `ufm_triage` — guided cluster triage workflow
- `ufm_investigate_port` — investigate specific ports on a system
- `ufm_log_search` — guided log search across log types
