<!-- mcp-name: io.github.togethercomputer/mcp-common -->

# NetBox MCP Server

[![CI](https://github.com/togethercomputer/mcp-common/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/togethercomputer/mcp-common/actions/workflows/smoke-test.yml)
[![Release](.github/badges/release.svg)](https://github.com/togethercomputer/mcp-common/releases)
[![Tests](https://github.com/togethercomputer/mcp-common/actions/workflows/test.yml/badge.svg)](https://github.com/togethercomputer/mcp-common/actions/workflows/test.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![MCP](https://img.shields.io/badge/MCP-modelcontextprotocol.io-blue.svg)](https://modelcontextprotocol.io)
[![Version](https://img.shields.io/github/v/release/togethercomputer/mcp-common)](https://github.com/togethercomputer/mcp-common/releases)

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for [NetBox](https://netbox.dev/). Query your infrastructure data -- devices, IPs, sites, racks, VLANs, and more -- directly from LLMs that support MCP.

## Features

| Category | What You Get |
|----------|-------------|
| **8 MCP Tools** | Device lookup, OOB summary, filtered queries, global search, changelogs, object-by-ID, batch fetch, device update |
| **13 CLI Commands** | `netbox-cli` — `lookup-device`, `oob-summary`, `get-object-by-id`, `get-objects-by-ids` (synthesized via `mcp_common.dual_mode`); plus hand-written `search`, `list`, `devices`, `sites`, `clusters`, `ips`, `changelogs`, `types`, `update-device` |
| **4 Resource Templates** | Browse `netbox://device/{hostname}`, `netbox://site/{slug}`, `netbox://ip/{address}`, `netbox://rack/{site}/{name}` |
| **3 Static Resources** | Object type catalog, server info, health check |
| **5 Workflow Prompts** | Investigate device, audit site, troubleshoot connectivity, inventory report, find available IPs |
| **Dynamic Credentials** | 1Password `op://` references resolved at runtime with cross-process kernel keyring caching |
| **Structured Output** | JSON schemas on tool responses for reliable LLM parsing |
| **Token Optimization** | Field filtering reduces responses by 80-90% |
| **Cross-MCP Ready** | Central lookup for Redfish, MAAS, and AWX MCP servers |

## CLI (`netbox-cli`)

A companion CLI that provides the same capabilities as the MCP server via shell commands. AI agents use this for ~40-90% fewer tokens than MCP tool calls.

### Installation

```bash
uv tool install git+https://github.com/togethercomputer/mcp-common
```

### Commands

| Command | Description |
|---------|-------------|
| `lookup-device <name>` | Resolve device by hostname, provider machine ID, or IP address (synthesized by `mcp_common.dual_mode`) |
| `oob-summary <name>` | Compact OOB-management view (Pydantic model) — just the IPs agents need |
| `get-object-by-id <type> <id>` | Get a single object by type and numeric ID |
| `get-objects-by-ids <type> <id>...` | Batch-fetch multiple objects by ID in a single call |
| `search <query>` | Search across multiple object types; auto-expands cluster matches |
| `list <type>` | List objects with filters, pagination, field projection |
| `devices` | Shortcut for `list dcim.device` with cluster/site/status filters |
| `sites` | Shortcut for `list dcim.site` |
| `clusters` | Shortcut for `list virtualization.cluster` |
| `ips` | Shortcut for `list ipam.ipaddress` |
| `changelogs` | Recent change history with user/action filters |
| `types` | List all supported NetBox object types |
| `update-device` | Update device status or cluster (write, requires `--confirm`) |

The `lookup-device`, `oob-summary`, `get-object-by-id`, and
`get-objects-by-ids` commands are synthesized at startup by
[`mcp_common.dual_mode.build_cli_from_mcp`](https://github.com/togethercomputer/mcp-common)
from the same `@dual_mode_tool`-decorated functions the MCP server exposes.
The remaining commands are hand-written — see the *Dual-mode tool framework*
note below for why.

### Examples

```bash
# Look up a device by hostname (dual-mode: same function as netbox_lookup_device)
netbox-cli lookup-device b65c909e-41 --json

# Compact OOB summary — Pydantic-typed shape with just the IPs agents need
netbox-cli oob-summary b65c909e-41 --status-filter active --json

# Batch-fetch several objects by ID in one call (variadic positional)
netbox-cli get-objects-by-ids dcim.device 4723 4724 4725 --json

# Search for all devices in a cluster (auto-expands cluster matches)
netbox-cli search cartesia5

# List devices filtered by site and status
netbox-cli devices --filter "site=ORI-TX,status=active" --limit 50

# Get specific fields only (token optimization)
netbox-cli list dcim.device --filter "cluster_id=152" --fields "name,status,primary_ip4" --json

# Update device status (write operation, requires VPN)
netbox-cli update-device gpu-node-01 --status offline --confirm
```

### Dual-mode tool framework

The read path is fully migrated to
[`mcp_common.dual_mode`](https://github.com/togethercomputer/mcp-common): each
`@dual_mode_tool`-decorated function serves **both** the MCP tool and the
synthesized `netbox-cli` command, so a single definition can't drift out of
sync. The CLI is assembled by `build_cli_from_mcp(...)`, and per-invocation
NetBox client setup runs through its `before_command` hook (so
`netbox-cli --help` works without credentials).

**Synthesized** (one function → MCP tool + CLI command):
`netbox_lookup_device`, `netbox_get_object_by_id`, `netbox_oob_summary`, and
`netbox_get_objects_by_ids`. Primary-identifier parameters use
`Annotated[T, typer.Argument()]` so they read positionally on the CLI
(`lookup-device HOST`, `get-object-by-id TYPE ID`,
`get-objects-by-ids TYPE ID...`) while the MCP tool input schema is
**unchanged** — FastMCP ignores the Typer marker when building the schema.

**Kept hand-written** (MCP tool + bespoke CLI command):

- `netbox_get_objects` and `netbox_get_changelogs` take a top-level `dict`
  `filters` parameter that the framework cannot project to a CLI option
  ([togethercomputer/mcp-common#111](https://github.com/togethercomputer/mcp-common/issues/111)),
  so forcing them through synthesis would crash the CLI at build time. Their
  CLI surface is the hand-written `list` / `devices` / `sites` / `clusters` /
  `ips` / `changelogs` commands (with repeatable `--filter key=value`).
  `netbox_get_objects` additionally has a `str | list[str]` (non-Optional
  union) `ordering` param the decorator rejects outright.
- `netbox_search_objects` stays hand-written so the `search` command keeps its
  cluster auto-expansion (resolving a matched cluster to its member devices
  and sites) — behavior the plain global-search tool does not implement.
- `netbox_update_device` (write) is out of scope for this read-only migration.

See [CHANGELOG.md](./CHANGELOG.md) for the full breakdown.

## Quick Start

### Cursor

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/install-mcp?name=netbox&config=eyJjb21tYW5kIjoidXYiLCJhcmdzIjpbIi0tZGlyZWN0b3J5IiwiL3BhdGgvdG8vbmV0Ym94LW1jcCIsInJ1biIsIm5ldGJveC1tY3AiXSwiZW52Ijp7Ik5FVEJPWF9VUkwiOiJodHRwczovL3lvdXItbmV0Ym94LmV4YW1wbGUuY29tLyIsIk5FVEJPWF9UT0tFTiI6InlvdXItYXBpLXRva2VuIn19)

After clicking, update `/path/to/netbox-mcp` and set your NetBox URL and API token.

### Claude Code

```bash
claude mcp add --transport stdio netbox \
  --env NETBOX_URL=https://your-netbox.example.com/ \
  --env NETBOX_TOKEN=your-api-token \
  -- uv --directory /path/to/netbox-mcp run netbox-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
    "mcpServers": {
        "netbox": {
            "command": "uv",
            "args": ["--directory", "/path/to/netbox-mcp", "run", "netbox-mcp"],
            "env": {
                "NETBOX_URL": "https://netbox.example.com/",
                "NETBOX_TOKEN": "your-api-token"
            }
        }
    }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `netbox_lookup_device` | **Recommended** -- Resolve a hostname to IPs (primary + OOB) in one call. Returns `oob_ip_address` for Redfish/BMC access. |
| `netbox_oob_summary` | Compact OOB-management view (`DeviceOOBSummary` Pydantic model) — just the IPs agents need for cross-MCP workflows. |
| `netbox_get_objects` | Query any object type with filters, pagination, ordering, and field projection. |
| `netbox_get_objects_by_ids` | Batch fetch multiple objects by ID in a single call. |
| `netbox_get_object_by_id` | Get a single object by its numeric ID. |
| `netbox_get_changelogs` | Audit trail / change history with time and user filters. |
| `netbox_search_objects` | Global search across devices, sites, IPs, VLANs, and more. Reports progress per type. |
| `netbox_update_device` | Update device status or cluster assignment (write operation, requires VPN). |

> Supported object types are limited to core NetBox objects and won't work with plugin types.

## Resources & Templates

### Static Resources

| URI | Description |
|-----|-------------|
| `netbox://object-types` | All supported object types and API endpoints (for LLM discovery) |
| `netbox://server-info` | Server version, tools, and configuration |
| `netbox://health` | Health check with uptime and NetBox API connectivity |

### Resource Templates

| URI Pattern | Description |
|-------------|-------------|
| `netbox://device/{hostname}` | Device details with enriched IP fields |
| `netbox://site/{slug}` | Site details by slug |
| `netbox://ip/{address}` | IP address record lookup |
| `netbox://rack/{site_slug}/{rack_name}` | Rack lookup by site and name |

## Prompts

| Prompt | Description |
|--------|-------------|
| `investigate_device(hostname)` | Device investigation: status, interfaces, IPs, site context |
| `audit_site(site_name)` | Site audit: devices, racks, VLANs, prefixes, utilization |
| `troubleshoot_connectivity(device_a, device_b)` | Trace connectivity path between two devices |
| `inventory_report(site_name)` | Inventory summary: devices by role/type, rack utilization |
| `find_available_ips(prefix)` | Find allocated and available IPs within a prefix |

## Cross-MCP Integration

This server is the **central lookup service** for infrastructure MCPs. Other servers depend on it for hostname and IP resolution.

```text
# Redfish MCP -- always use oob_ip, NOT primary_ip
> netbox_lookup_device("gpu-node-01")  →  oob_ip_address: "192.168.196.12"
> redfish_get_info(host="192.168.196.12", ...)

# MAAS MCP -- use hostname or primary_ip
> netbox_lookup_device("compute-node-05")  →  primary_ip4_address: "10.20.30.40"
> maas_get_machines(hostname="compute-node-05")
```

## Configuration

Configuration precedence: **CLI > Environment > .env file > Defaults**

| Setting | Default | Required | Description |
|---------|---------|----------|-------------|
| `NETBOX_URL` | -- | Yes | Base URL of your NetBox instance |
| `NETBOX_TOKEN` | -- | Yes | API token for authentication |
| `TRANSPORT` | `stdio` | No | `stdio` or `http` |
| `HOST` | `127.0.0.1` | If HTTP | HTTP server bind address |
| `PORT` | `8000` | If HTTP | HTTP server port |
| `VERIFY_SSL` | `true` | No | Verify SSL certificates |
| `MCP_HTTP_ACCESS_TOKEN` | -- | No | Bearer token for HTTP transport auth |
| `LOG_LEVEL` | `INFO` | No | Logging verbosity |
| `LOG_JSON` | `false` | No | JSON-formatted log output (for containers) |

### Example .env

```env
NETBOX_URL=https://netbox.example.com/
NETBOX_TOKEN=your_api_token_here
LOG_LEVEL=INFO
```

### Credential Resolution

`NETBOX_TOKEN` supports either a plain token string or a 1Password `op://` reference:

```env
NETBOX_TOKEN=abc123def456...
NETBOX_TOKEN=op://Employee/Together - Netbox/NETBOX_TOKEN
```

For 1Password setup and how dynamic resolution works, see [mcp-common credential chain](https://github.com/togethercomputer/mcp-common#credential-chain).

### CLI

```bash
uv run netbox-mcp --help
uv run netbox-mcp --log-level DEBUG --no-verify-ssl   # Development
uv run netbox-mcp --transport http --port 9000        # HTTP mode
```

## HTTP Transport (Production)

For production and multi-tenant deployments, run the server in HTTP mode using the ASGI factory with uvicorn:

```bash
# Direct (for development)
uv run netbox-mcp --transport http --host 0.0.0.0 --port 8000

# Production (ASGI factory with uvicorn)
NETBOX_URL=https://netbox.example.com/ \
NETBOX_TOKEN=your-api-token \
uvicorn netbox_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
```

The MCP endpoint is at `/mcp` and a health check is at `/health`.

### Access Token Authentication

Set `MCP_HTTP_ACCESS_TOKEN` to require clients to authenticate with a bearer token:

```bash
export MCP_HTTP_ACCESS_TOKEN=my-secret-token
```

Clients must then send `Authorization: Bearer my-secret-token` or `X-API-Key: my-secret-token`.

### CORS

The ASGI factory (`create_app`) includes CORS middleware that exposes the `mcp-session-id` header, which is required for browser-based MCP clients like Cursor.

## Docker

```bash
docker build -t netbox-mcp:latest .

# HTTP mode (default for containers)
docker run --rm \
  -e NETBOX_URL=https://netbox.example.com/ \
  -e NETBOX_TOKEN=your-api-token \
  -e TRANSPORT=http \
  -p 8000:8000 \
  netbox-mcp:latest

# stdio mode
docker run --rm -i \
  -e NETBOX_URL=https://netbox.example.com/ \
  -e NETBOX_TOKEN=your-api-token \
  -e TRANSPORT=stdio \
  netbox-mcp:latest
```

The HTTP server is accessible at `http://localhost:8000/mcp`.

## Kubernetes (Helm)

A Helm chart is provided in `chart/`:

```bash
helm install netbox-mcp ./chart \
  --set netbox.url=https://netbox.example.com/ \
  --set netbox.token=your-api-token \
  --set mcpHttpAccessToken=your-mcp-token
```

Or with an existing secret:

```bash
kubectl create secret generic netbox-mcp-creds \
  --from-literal=NETBOX_TOKEN=your-api-token \
  --from-literal=MCP_HTTP_ACCESS_TOKEN=your-mcp-token

helm install netbox-mcp ./chart \
  --set netbox.url=https://netbox.example.com/ \
  --set existingSecret=netbox-mcp-creds
```

The chart configures health/readiness probes on `/health`, resource limits, and supports custom environment variables via `extraEnv`.

## Field Filtering (Token Optimization)

Use the `fields` parameter on any query tool to reduce token usage by 80-90%:

```python
# Full response: ~5000 tokens
netbox_get_objects('dcim.device', {'site_id': 1})

# With fields: ~500 tokens
netbox_get_objects('dcim.device', {'site_id': 1}, fields=['id', 'name', 'status', 'site'])
```

## Development

```bash
uv sync --all-groups          # Install dependencies (includes mcp-common)
uv run pytest -v              # Run tests
uv run ruff check src/ tests/ # Lint
uv run ruff format src/ tests/ # Format
uv run mypy src/              # Type check
```

This project uses [conventional commits](https://www.conventionalcommits.org/) for automated versioning via `python-semantic-release`.

### Integration tests against a local NetBox simulator

The unit suite runs against mocked HTTP. For higher-fidelity coverage of real
NetBox semantics (filtering, pagination, ordering, structured output, and the
`update-device` **write** path) there is a self-contained simulator under
`tests/integration/` that boots a real **NetBox v4.3.2** in Docker (stock
images, no build), seeds a small synthetic topology via the REST API, and runs
the real `netbox-mcp` tool functions against it.

These tests carry the `integration` marker and are **excluded** from the fast
gate (`pytest -m "not integration and not e2e and not slow"`), so they never
slow down normal runs.

```bash
# Run the integration suite (boots + seeds + tears down its own stack)
make integration
# equivalently:
uv run pytest -m integration

# Manual exploration: bring the simulator up on a fixed port and seed it
make sim-up        # NetBox API at http://127.0.0.1:8080 (admin / admin)
make sim-seed      # re-run the idempotent seed
make sim-logs      # tail the netbox container
make sim-down      # stop and remove volumes
```

Env knobs:

| Variable | Default | Purpose |
|----------|---------|---------|
| `NETBOX_REQUIRE_DOCKER` | unset | When `1`, missing/unavailable Docker **fails** the tests instead of skipping (set in CI). |
| `NETBOX_IT_CLEAN` | `1` under `CI`/`GITHUB_ACTIONS`, else `0` | When truthy, the DB volume is removed on teardown (`down -v`); otherwise it is kept for fast local re-runs. |
| `CONTAINER_RUNTIME` | `docker` | Container runtime for the stack (`podman` also works). |
| `NETBOX_SIM_PORT` | random free port (fixture); `8080` (`make sim-up`) | Host port the NetBox API is published on. |

If Docker is unavailable and `NETBOX_REQUIRE_DOCKER` is not set, the suite skips
cleanly (it does not fail). The same `docker-compose.yaml` and `seed.py` are
used by both the pytest fixture and the `make` targets, so local and CI runs
cannot drift. A dedicated CI job (`netbox-integration`) runs this suite on every
PR and push to `dev`/`main`.

## License

[Apache 2.0](LICENSE)
