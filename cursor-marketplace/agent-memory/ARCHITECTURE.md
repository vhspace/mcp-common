# agent-memory — Architecture & Developer Guide

## What This Is

A long-term memory system for AI agents (Cursor, Claude Code, etc.) that persists knowledge across sessions. Instead of keeping everything in flat markdown files that degrade over time, this system stores knowledge in a **temporal knowledge graph** where facts have timestamps, relationships, and can be superseded when new information arrives.

The agent interacts with it via a `mem` CLI command available in the shell.

## Why It Exists

Without this system, each agent session starts from zero. An agent that triaged an NVLink error last week has no memory of it this week. This system solves that by giving agents a shared, searchable knowledge base that grows over time.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      Host (devcontainer)                  │
│                                                          │
│   Agent (Cursor/Claude) ──── mem CLI ─────┐              │
│                                           │              │
│   ┌───────────────────────────────────────┼──── Docker ──┤
│   │                                       ▼              │
│   │   ┌─────────────────────────────────────────┐        │
│   │   │          memory-server (Python)          │        │
│   │   │                                         │        │
│   │   │  src/cli.py      ← typer CLI            │        │
│   │   │  src/server.py   ← MCP server (FastMCP) │        │
│   │   │  src/backend.py  ← shared service layer │        │
│   │   │  src/config.py   ← pydantic-settings    │        │
│   │   │  src/decay.py    ← retention scoring     │        │
│   │   └──────────┬──────────────────────────────┘        │
│   │              │                                       │
│   │              ▼                                       │
│   │   ┌──────────────────┐                               │
│   │   │   Neo4j 5.26     │                               │
│   │   │   (graph DB)     │                               │
│   │   │   port 7687      │                               │
│   │   └──────────────────┘                               │
│   └──────────────────────────────────────────────────────┘
│                                                          │
│   External APIs:                                         │
│     Anthropic (Claude Sonnet 4.6) → entity extraction    │
│     Together AI → embeddings (multilingual-e5-large)     │
└──────────────────────────────────────────────────────────┘
```

## Components

### 1. Neo4j (Graph Database)
- Stores everything: entities, relationships, episodes, embeddings
- Runs as a Docker container on port 7687 (Bolt) / 7474 (HTTP browser)
- Data persists in `neo4j-data` Docker volume
- Auth: `neo4j` / `changeme123`

### 2. memory-server (Python container)
Contains both the CLI and the MCP server, sharing a single backend.

#### `src/config.py` — Settings
Pydantic-settings class loading from environment variables. Key settings:
- `LLM_PROVIDER` / `MODEL_NAME` — which LLM does entity extraction (default: Anthropic Claude Sonnet 4.6)
- `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` — which service creates vector embeddings (default: Together AI)
- `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` — database connection
- `GROUP_ID` — default memory group for scoping

#### `src/backend.py` — Shared Service Layer (the core)
Wraps `graphiti-core` (the Graphiti SDK). Both the CLI and MCP server call into this class. Key methods:

| Method | What it does |
|--------|-------------|
| `add_episode(name, body, ...)` | Ingests text → Claude extracts entities & relationships → Together generates embeddings → stored in Neo4j |
| `search_facts(query, ...)` | Embeds the query → hybrid search (vector + BM25 + graph traversal) → returns matching facts |
| `search_nodes(query, ...)` | Same but returns entity nodes instead of relationship edges |
| `get_episodes(group_id, last_n)` | Lists recent episodes (raw ingested documents) |
| `get_status()` | Health check |
| `retention_score(...)` | Static method: hybrid Ebbinghaus + usage-based decay scoring |

When no `group_ids` are specified for search, it searches **all groups** via `_all_group_ids()`.

#### `src/cli.py` — Typer CLI
Thin wrapper around the backend. Commands:

```
mem search <query> [--group-id] [--max-results] [--json]
mem add <name> <body> [--source] [--group-id] [--json]
mem episodes [--group-id] [--last-n] [--json]
mem status [--json]
mem decay [--dry-run/--execute] [--json]
mem promote [--output-dir] [--json]
mem ingest <path> [--group-id] [--recursive] [--json]
```

All commands support `--json` for machine-readable output. Default is rich-formatted tables on stderr.

#### `src/server.py` — MCP Server (FastMCP)
Exposes the same backend as MCP tools over SSE on port 8000. Tools: `memory_search`, `memory_search_nodes`, `memory_add`, `memory_episodes`, `memory_status`. Currently disabled in `.cursor/mcp.json` in favor of the CLI (lower token cost).

#### `src/decay.py` — Retention Scoring & Rule Promotion
- `run_decay_cycle()` — evaluates retention scores for all episodes
- `promote_memories_to_rules()` — exports high-confidence procedural memories as `.cursor/rules/*.mdc` files

### 3. `bin/mem` — Host CLI Wrapper
A shell script at `/usr/local/bin/mem` that routes commands into the Docker container:
```sh
#!/bin/sh
exec docker compose -f /workspaces/together/agent-memory/docker-compose.yml \
  exec -T memory-server python -m src.cli "$@" 2>&1 \
  | grep -v 'Received notification' | grep -v 'GqlStatusObject'
```

### 4. `scripts/seed_workspace.py` — Data Seeder
Ingests existing workspace markdown files into the graph. Categorizes by group:
- `incidents` — on-call notes, incident resolutions
- `infrastructure` — bringup docs, hardware procedures
- `design` — architecture documents

Includes a content sanitizer that redacts lines matching API key / token patterns before ingestion.

## How Data Flows

### Ingestion (mem add)
```
Text input
  → Anthropic Claude: extracts entities (people, systems, concepts)
  → Anthropic Claude: extracts relationships (facts between entities)
  → Together AI: generates 1024-dim embeddings for each entity/fact
  → Neo4j: stores nodes, edges, embeddings, and temporal metadata
```
Each episode takes ~15-60 seconds (API latency).

### Search (mem search)
```
Query text
  → Together AI: embed the query (1024-dim vector)
  → Neo4j: parallel search strategies:
      1. Vector similarity (kNN on embeddings)
      2. BM25 full-text (keyword matching on facts)
      3. Graph traversal (relationship-aware context)
  → Results merged, deduplicated, ranked
  → Returned as fact list with temporal metadata
```
Search takes ~2-3 seconds.

### Temporal Supersession (Graphiti built-in)
When new facts contradict old ones:
- Old edge gets `invalid_at` timestamp set
- New edge created with updated fact
- Both remain in graph (history preserved)
- Search excludes superseded facts by default

## Data Model (in Neo4j)

### Node Types
- **Entity** — extracted people, systems, services, concepts. Has `name`, `summary`, `name_embedding`, `group_id`
- **Episodic** — raw ingested documents. Has `name`, `content`, `source`, `valid_at`, `group_id`
- **Community** — auto-detected clusters of related entities
- **Saga** — grouping of related episodes

### Edge Types
- **RELATES_TO** — factual relationships between entities. Has `fact`, `fact_embedding`, `valid_at`, `invalid_at`, `group_id`
- **MENTIONS** — links episodes to entities they reference
- **HAS_MEMBER** — links communities to their member entities

### Group IDs
Every node and edge has a `group_id` that scopes it. This allows searching within a domain or across all domains.

## Configuration

All via environment variables (loaded from `agent-memory/.env`):

| Variable | Current Value | Purpose |
|----------|--------------|---------|
| `LLM_PROVIDER` | `anthropic` | Entity extraction LLM |
| `MODEL_NAME` | `claude-sonnet-4-6` | Specific model |
| `EMBEDDING_PROVIDER` | `together` | Embedding service |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large-instruct` | 1024-dim embeddings |
| `ANTHROPIC_API_KEY` | (set) | Claude API access |
| `TOGETHER_API_KEY` | (set) | Together AI access |
| `NEO4J_URI` | `bolt://neo4j:7687` | Database connection |
| `GROUP_ID` | `together-ops` | Default group for adds |

## Current Scale (as of initial deployment)

- 52 episodes ingested (on-call notes, infra docs, design docs)
- 851 entities, 1,456 facts in the graph
- 18 MB actual graph data + 514 MB transaction logs
- Growth rate: ~350 KB per episode

## Key Files

```
agent-memory/
├── .env                    # API keys and config (not committed)
├── .env.example            # Template
├── docker-compose.yml      # Neo4j + memory-server
├── Dockerfile              # Python 3.12 multi-stage build
├── pyproject.toml          # Dependencies (graphiti-core, mcp, typer, etc.)
├── bin/mem                 # Host CLI wrapper (symlinked to /usr/local/bin/mem)
├── src/
│   ├── config.py           # Pydantic settings
│   ├── backend.py          # Core service layer (Graphiti wrapper)
│   ├── server.py           # MCP server (FastMCP, SSE on :8000)
│   ├── cli.py              # Typer CLI (mem command)
│   └── decay.py            # Retention scoring + rule promotion
├── scripts/
│   └── seed_workspace.py   # Bulk ingest workspace data
└── ARCHITECTURE.md         # This file
```

External:
- `.cursor/rules/agent-memory-cli.mdc` — Cursor rule teaching agents to use `mem`
- `.devcontainer/setup-ai-agents.sh` — starts the stack on workspace create

## Developing / Debugging

```bash
# Check status
mem status

# View Neo4j browser (if port-forwarded)
# http://localhost:7474  (neo4j/changeme123)

# Raw Neo4j queries
docker compose -f agent-memory/docker-compose.yml exec -T neo4j \
  cypher-shell -u neo4j -p changeme123 "MATCH (n:Entity) RETURN n.name LIMIT 10"

# Rebuild after code changes
cd agent-memory && docker compose build memory-server && docker compose up -d memory-server

# View container logs
docker compose -f agent-memory/docker-compose.yml logs memory-server --tail 50

# Python shell inside container
docker compose -f agent-memory/docker-compose.yml exec -T memory-server python3
```

## Known Limitations

1. **Search latency**: ~2-3 seconds per query (embedding API call is the bottleneck)
2. **Ingestion cost**: Each episode requires 2-3 Claude API calls + multiple embedding calls (~$0.01-0.05 per episode)
3. **No automatic pruning**: Decay scoring exists but doesn't delete yet — thresholds need tuning on real data first
4. **Neo4j transaction logs**: Grow to ~500 MB; can be compacted with `CALL db.checkpoint()` but no auto-rotation configured
5. **Single-node Neo4j**: Community edition, no clustering. Fine for this scale.

## Future Work

- Wire `mem decay --execute` to actually prune low-retention nodes
- Periodic rule promotion (`mem promote` on a schedule)
- Webhook ingestion from PagerDuty / Linear / Grafana
- Together AI as LLM provider (replace Anthropic to reduce vendor count)
- Helm chart for Kubernetes deployment
- Access count tracking on edges for better usage-based decay
