---
name: agent-memory-usage
description: Use when searching for prior knowledge, storing incident resolutions, or managing long-term agent memory. Triggers on memory, remember, prior incidents, knowledge base, search history, past resolutions.
---

# Agent Memory Skill

## When to Use
- Before starting any task: search for prior knowledge
- After resolving incidents: store what was learned
- When asked about past incidents, patterns, or resolutions
- When debugging recurring issues

## How It Works
The agent-memory system stores knowledge in a temporal knowledge graph (Neo4j + Graphiti).
Facts have timestamps and can be superseded when new information arrives.

## Commands (via Shell)

### Search for knowledge
```bash
mem search "<query>"
```
Searches ALL groups (incidents, infrastructure, design, together-ops) by default.
Returns facts with temporal metadata (valid_at, superseded status).

To narrow to a specific group:
```bash
mem search "<query>" --group-id incidents
```

### Store new knowledge
```bash
mem add "<name>" "<what was learned>" --group-id incidents
```

Group IDs:
- `incidents` — on-call incident resolutions and patterns
- `infrastructure` — hardware, networking, cluster knowledge
- `design` — architecture and design decisions
- `together-ops` — general operations (default)

### Check recent context
```bash
mem episodes --last-n 5
```

### Health check
```bash
mem status
```

## Workflow

1. **Search first**: Always run `mem search` before starting work on an incident or task
2. **Act on findings**: If prior knowledge exists, use it to inform your approach
3. **Store learnings**: After resolving something, run `mem add` with the key takeaway
4. **Be specific**: Include hostnames, ticket IDs, firmware versions, and root causes in stored memories

## Example

```bash
# Before triaging a GPU error
mem search "NVLink CRC errors H100"

# After resolving it
mem add "nvlink-crc-fix-2026-03" \
  "NVLink CRC errors on H100 nodes in us-south-2a caused by firmware 24.04. Fixed by upgrading to 24.07 via Redfish." \
  --group-id incidents
```

## Notes
- Search takes ~2-3 seconds (embedding API latency)
- Adding takes ~15-60 seconds (LLM entity extraction)
- All data persists in Neo4j across sessions
- Superseded facts are preserved but excluded from default search
