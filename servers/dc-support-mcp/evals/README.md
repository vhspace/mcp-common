# DC Support MCP Eval Suite

Evaluation scenarios for `dc-support-mcp`, testing agents in **read-only** MCP and CLI modes using [Inspect AI](https://inspect.ai-safety-institute.org.uk/).

## Prerequisites

```bash
cd servers/dc-support-mcp
uv sync --group dev --extra eval
export MCP_ENFORCE_READONLY=1          # required — write-safety preflight aborts without it
export TOGETHER_API_KEY="your-key"     # model-under-test + LLM-as-judge

# At least one vendor credential set (literal or op:// reference):
export ORI_PORTAL_USERNAME="you@together.ai"
export ORI_PORTAL_PASSWORD="op://Together/ORI Portal/password"
# OR: IREN_FRESHDESK_API_KEY, HYPERTEC_PORTAL_*, etc.

# Optional judge decoupling (mcp-common #132)
export EVAL_JUDGE_API_KEY="separate-judge-key"
```

**Live vendor portals required** for ticket-list scenarios. Without configured credentials, preflight fails fast instead of scoring ~0 across every model. See `docs/CREDENTIALS.md` for the full variable list.

**Credential resolution:** Portal passwords may be `op://` references. The eval parent resolves them once (via `op` / `OP_SERVICE_ACCOUNT_TOKEN`) and forwards plain values to the spawned `dc-support-mcp` child — the child cannot resolve `op://` on its own.

## Eval modes

| Mode | File | What it tests |
|------|------|---------------|
| **MCP** | `mcp_eval.py` | Agent uses read-only MCP tools only (explicit allow-list; no write tools) |
| **CLI** | `cli_eval.py` | Agent uses `dc-support-cli` via one-shot `bash` |

There is no combined mode — dc-support can file vendor tickets and the skeleton focuses on read-only surface validation.

## Write safety

This suite **must not file real vendor tickets**. Protections:

1. Exposes only an explicit **read-only MCP allow-list** (`read_only_tools`).
2. Runs **`assert_read_only_eval_mode()`** before any model (checks `MCP_ENFORCE_READONLY` + server middleware).
3. Uses scenarios limited to **auth-status**, **vendors**, and **list tickets** — no create/comment/update scenarios.

## Running

```bash
# Dry-run plan (no model spend)
uv run python evals/run_matrix.py --dry-run --skip-preflight --tier fast --mode cli

# Cheap smoke (needs live credentials + keys)
uv run python evals/run_matrix.py --tier fast --mode cli --limit 2

# Single task via inspect
inspect eval evals/cli_eval.py --limit 1
```

## Model matrix

Registry: `models.py`. Runner: `run_matrix.py` (thin wrapper over `mcp_common.testing.eval.matrix_runner`).

## Blockers / notes

- **No vendor creds:** preflight aborts; use `--skip-preflight` only for dry structural checks.
- **auth-status / vendors** are CLI-only commands (no MCP tool equivalent).
- **No K8s sandbox:** CLI eval uses inspect's local bash sandbox with repo `dc-support-cli` on `$PATH`.
