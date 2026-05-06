# agent-memory — Long-Term Memory for AI Agents

[![CI](https://github.com/vhspace/agent-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/agent-memory/actions/workflows/ci.yml)
[![Release](.github/badges/release.svg)](https://github.com/vhspace/agent-memory/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

A Graphiti-backed temporal knowledge graph that gives AI agents persistent, decaying memory with automatic rule promotion. Supports both MCP (for agent tool calling) and CLI (for token-efficient shell access). Runs in Docker.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│  CLI (typer) │────▶│  Shared Backend   │◀────│  MCP Server  │
│  `mem`       │     │  (Graphiti Core)  │     │  (FastMCP)   │
└─────────────┘     └────────┬─────────┘     └──────────────┘
                             │
                    ┌────────┴────────┐
                    │    FalkorDB     │
                    │  (Redis-based   │
                    │   graph DB)     │
                    └─────────────────┘
```

## Quick Start

1. Copy `.env.example` to `.env`, fill in API keys
2. `docker compose up -d`
3. Seed with workspace data: `docker compose exec memory-server python -m scripts.seed_workspace`
4. Search: `docker compose exec memory-server mem search "NVLink errors"`

## MCP Integration (for Cursor)

Add to your Cursor MCP config:

```json
{
  "mcpServers": {
    "agent-memory": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

## CLI Usage

| Command | Example | Description |
|---------|---------|-------------|
| `mem search` | `mem search "NVLink errors"` | Search facts and relationships |
| `mem search --json` | `mem search "BMC firmware" --json` | Search with JSON output |
| `mem add` | `mem add "NVLink fix" "Disable X in BIOS" --group-id incidents` | Store knowledge |
| `mem add --source` | `mem add "Doc" "..." --source text` | Store with source type |
| `mem episodes` | `mem episodes` | List recent context |
| `mem episodes --last-n` | `mem episodes --last-n 5` | Last N episodes |
| `mem forget` | `mem forget "stale fact" --group-id infra` | Remove incorrect/stale facts |
| `mem forget --entity` | `mem forget --entity "node-xyz" --yes` | Remove all facts about an entity |
| `mem forget --dry-run` | `mem forget "old fact" --dry-run` | Preview removal without deleting |
| `mem status` | `mem status` | Health check |
| `mem promote` | `mem promote` | Export learned procedures to Cursor rules |

## MCP Tools

| Tool | Description |
|------|-------------|
| `mem_search` | Search the knowledge graph for facts and relationships |
| `mem_add` | Add a new fact or episode to memory |
| `mem_episodes` | Retrieve recent episodic context |
| `mem_status` | Check memory service health |
| `mem_promote` | Promote high-confidence procedures to Cursor rules |

## Memory Model

- **Episodic**: Time-stamped events (incidents, alerts, resolutions)
- **Semantic**: Entities and relationships (devices, services, people)
- **Procedural**: Learned rules auto-promoted to `.cursor/rules/`

## Memory Decay

- **Temporal supersession** (Graphiti built-in): Old facts marked invalid when contradicted
- **Hybrid decay scoring**: Ebbinghaus forgetting curve + usage-based reinforcement
- **Rule promotion**: High-confidence procedures exported to Cursor rules every 6 hours

## Skills Integration — Storing Learnings

Operational skills (gpu-diag, dc-support, redfish, fabric-monitoring, etc.) should
include a memory storage step after resolving incidents or discovering new knowledge.
This ensures learnings persist across sessions and agents.

### Template for Skills

Add this step at the end of your skill's resolution workflow:

```bash
mem add "<short-title>" "<what-was-learned>" --group-id <group-id>
```

### Group ID Reference

| Skill / Domain | Group ID |
|----------------|----------|
| GPU diagnostics | `incidents` |
| DC vendor support | `incidents` |
| Redfish/BMC operations | `infrastructure` |
| Fabric monitoring (UFM) | `infrastructure` |
| MAAS provisioning | `infrastructure` |
| Storage (Weka) | `infrastructure` |
| Architecture decisions | `design` |
| General operations | `together-ops` |

### Skill Step Examples

**GPU Diagnostics** — after resolving a GPU issue:
```bash
mem add "xid-79-fix-node-abc" \
  "Node abc had XID 79 (GPU fallen off bus). Fixed by cold reboot and BIOS update to v2.4." \
  --group-id incidents
```

**Redfish Operations** — after discovering a BMC pattern:
```bash
mem add "supermicro-bmc-reset-pattern" \
  "Supermicro X13 BMC requires 30s wait after reset before Redfish API responds." \
  --group-id infrastructure
```

**Fabric Monitoring** — after resolving an IB fabric issue:
```bash
mem add "ufm-link-flap-workaround" \
  "Port 15 on switch leaf-42 flaps under load. Workaround: set link speed to HDR instead of NDR." \
  --group-id infrastructure
```

### Correcting Mistakes with `mem forget`

If a skill stores incorrect information, use `mem forget` to remove it:
```bash
mem forget "node abc is in cluster Y" --group-id incidents --yes
mem forget --entity "node-abc" --group-id incidents --dry-run
```

## Data Sources (for seeding)

- On-call incident notes
- Infrastructure bringup docs
- Hardware documentation
- Design documents

## Development

- `pip install -e .` for local dev
- `docker compose up falkordb` to run just the DB
- Run tests: `pytest`

## Future / Roadmap

- Kubernetes deployment (Helm chart)
- PagerDuty webhook ingestion
- Linear/Grafana auto-ingestion
- Access count tracking for better decay scoring
- Together AI inference backend (replace OpenAI)
