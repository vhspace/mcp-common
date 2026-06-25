# AWX MCP Eval Suite

Evaluation scenarios for `awx-mcp`, testing agents in **read-only** MCP and CLI modes using [Inspect AI](https://inspect.ai-safety-institute.org.uk/).

## Prerequisites

```bash
cd servers/awx-mcp
uv sync --group dev --extra eval
export AWX_TOKEN="your-token"          # literal or op:// reference
export AWX_HOST="https://awx.internal.together.ai/"  # optional; this is the default
export MCP_ENFORCE_READONLY=1          # required — write-safety preflight aborts without it
export TOGETHER_API_KEY="your-key"     # model-under-test + LLM-as-judge

# Optional judge decoupling (mcp-common #132)
export EVAL_JUDGE_API_KEY="separate-judge-key"
```

**Live AWX required.** Scenarios query real jobs/inventories (inventory id 256 = `research-common-h100` in production AWX). Without `AWX_TOKEN` resolved in the parent shell, preflight fails fast instead of scoring ~0 across every model.

**Credential resolution:** `AWX_TOKEN` may be an `op://` reference. The eval parent resolves it once (via `op` / `OP_SERVICE_ACCOUNT_TOKEN`) and forwards a plain token to the spawned `awx-mcp` child and the CLI bash sandbox — the child cannot resolve `op://` on its own.

## Eval modes

| Mode | File | What it tests |
|------|------|---------------|
| **MCP** | `mcp_eval.py` | Agent uses read-only AWX MCP tools only (explicit allow-list; no launch/write tools) |
| **CLI** | `cli_eval.py` | Agent uses `awx-cli` via one-shot `bash` |

There is no combined mode in Phase 1 — AWX is write-capable and the skeleton focuses on read-only surface validation.

## Write safety

AWX can launch jobs and mutate resources. This suite:

1. Exposes only an explicit **read-only MCP allow-list** to the model (`read_only_tools`).
2. Runs **`assert_read_only_eval_mode()`** before any model (checks `MCP_ENFORCE_READONLY` + server middleware).
3. Uses scenarios that never expect `awx_launch` or other write tools.

## Running

```bash
# Dry-run plan (no model spend)
uv run python evals/run_matrix.py --dry-run --skip-preflight --tier fast --mode mcp

# Cheap smoke (needs live AWX + keys)
uv run python evals/run_matrix.py --tier fast --mode mcp --limit 2

# Single task via inspect
inspect eval evals/mcp_eval.py --limit 1
```

## Model matrix

Registry: `models.py`. Runner: `run_matrix.py` (thin wrapper over `mcp_common.testing.eval.matrix_runner`).

Anthropic Haiku (fast) and Sonnet (medium) entries are gated on `ANTHROPIC_API_KEY` per mcp-common #156.

## Blockers / notes

- **No AWX creds:** preflight aborts with a clear error; use `--skip-preflight` only for dry structural checks.
- **Inventory 256:** scenarios assume production AWX still has inventory id 256; adjust `scenarios.json` if your AWX instance differs.
- **No K8s sandbox:** CLI eval uses inspect's local bash sandbox with repo `awx-cli` on `$PATH`.

## Trend history & nightly smoke (#88 Phase 3b/3c)

Append each run's `summary.json` to an append-only `history.jsonl` and render a
release-over-release trend (off by default — CI does not append):

```bash
MCP_ENFORCE_READONLY=1 uv run python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
    --history evals/results/history.jsonl --trend-dir evals/results/trend
```

`--trend-dir` writes `trend.md` (Markdown table + Mermaid `xychart`, both
GitHub-inline) and `sections.json`. See `docs/EVALS.md` for the full guide.

The recommended **nightly smoke** (cheap, on-demand — not a scheduled CI
workflow, to avoid unattended model spend) is the command above:
`--tier fast --mode mcp --limit 5`. Run it by hand or from your own scheduler.
