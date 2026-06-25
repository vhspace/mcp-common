# NetBox MCP Eval Suite

Evaluation scenarios for the `netbox-mcp` server, testing agents across three execution modes using [Inspect AI](https://inspect.ai-safety-institute.org.uk/).

## Prerequisites

```bash
uv sync --group dev --extra eval
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="your-token"
export TOGETHER_API_KEY="your-key"  # model-under-test + LLM-as-judge scoring

# Optional — decouple the LLM-as-judge onto its own key/budget/endpoint
# (mcp-common #132). When EVAL_JUDGE_API_KEY is set the judge no longer shares
# the model-under-test's rate budget, so run_matrix.py auto-bumps the default
# --max-connections (see "Serial execution & the judge" below).
export EVAL_JUDGE_API_KEY="separate-judge-key"      # else falls back to TOGETHER_API_KEY
export EVAL_JUDGE_BASE_URL="https://api.together.xyz/v1"  # else the default Together endpoint
# EVAL_JUDGE_MODEL is set automatically by run_matrix.py (from --judge-model).
```

## Eval Modes

| Mode | File | What it tests |
|------|------|---------------|
| **MCP** | `mcp_eval.py` | Agent uses `netbox-mcp` MCP tools only |
| **CLI** | `cli_eval.py` | Agent uses `netbox-cli` shell commands only |
| **Combined** | `combined_eval.py` | Agent has both MCP + CLI; should prefer CLI |

## Running Evals

```bash
# MCP-only mode
inspect eval evals/mcp_eval.py

# CLI-only mode
inspect eval evals/cli_eval.py

# Combined mode (tests interface preference)
inspect eval evals/combined_eval.py

# Run with a specific model
inspect eval evals/mcp_eval.py --model together/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
```

> **Note:** Evals require a live NetBox backend (they will not pass against a mock).
> Reads work **off-VPN**: the client sends a `python-requests` User-Agent, which the
> Cloudflare WAF allows. Only the default `Python-urllib/*` User-Agent is blocked
> (Cloudflare Error 1010), so plain `urllib` calls 403 off-VPN while `netbox-mcp`/`netbox-cli` do not.

## Model matrix

Run the scenarios across many models (fast/cheap → medium → high) and modes with one
command. The registry lives in `models.py` (edit it to add/flip models); the runner is
`run_matrix.py`.

### Tiers

| Model (inspect string) | Tier | Weights | Status |
|------------------------|------|---------|--------|
| `together/Qwen/Qwen3.5-9B` | fast | open | live |
| `together/meta-llama/Llama-3.3-70B-Instruct-Turbo` | fast | open | live |
| `together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | medium | open | live (also the **judge**) |
| `together/deepseek-ai/DeepSeek-V4-Pro` | medium | open | live |
| `together/MiniMaxAI/MiniMax-M2.7` | medium | open | live |
| `openai-api/together/Qwen/Qwen3.7-Max` | high | open | live — **streaming-required**, runs via the `openai-api` route (see below) |
| `together/moonshotai/Kimi-K2.6` | high | open | live — serverless Kimi-2.5-class (replaces non-serverless `Kimi-K2.5-fp4`) |
| `together/moonshotai/Kimi-K2.5-fp4` | high | open | **disabled** — non-serverless (400 `model_not_available`); needs a dedicated endpoint |
| `openai/gpt-5.5` | high | closed | needs `OPENAI_API_KEY` |
| `anthropic/claude-opus-4-8` | high | closed | needs `ANTHROPIC_API_KEY` |
| `cursor/composer-2.5` | high | agentic | disabled (needs a Cursor-SDK bridge — deferred) |

> Model strings use inspect's `provider/model` form. Together models are
> `together/<together-api-model-string>` (inspect strips `together/` and sends the rest to
> `https://api.together.xyz/v1`). Confirmed against `inspect_ai` 0.3.211.

#### Streaming-required models → the `openai-api` route

Some Together models **require** `stream=true` — a non-streaming request 400s with
`streaming_required` (confirmed live for `Qwen/Qwen3.7-Max`). The inspect `together/`
provider **cannot stream** in 0.3.211: `TogetherAIAPI` always calls the non-streaming
`client.chat.completions.create()`, its constructor rejects a `stream` arg, and
`GenerateConfig` has no `stream` field. The generic `openai-api` provider
(`OpenAICompatibleAPI`) *does* honor a `stream` flag.

So such models are registered via the generic provider:

- **Model string:** `openai-api/together/<slug>` (e.g. `openai-api/together/Qwen/Qwen3.7-Max`).
  The provider derives `service=together` from the first path segment and auto-resolves
  `TOGETHER_API_KEY`.
- **`TOGETHER_BASE_URL=https://api.together.xyz/v1` is required** — the generic provider has
  no built-in Together base URL. `run_matrix.py` exports it automatically (via `setdefault`,
  so an explicit user value is preserved); the native `together/` provider falls back to the
  same URL, so setting it globally is safe.
- **`stream=True`** is delivered as a provider-constructor arg through
  `inspect_ai.eval(model_args={"stream": True})` (carried on the registry entry's
  `model_args`), **not** a `GenerateConfig` field.
- The registry entry also carries `catalog_slug` (the bare `Qwen/Qwen3.7-Max`) so the
  API-key/catalog gate still resolves — `together_api_model("openai-api/...")` is `None`.

> The `Kimi-K2.5-fp4` → `moonshotai/Kimi-K2.6` swap: `Kimi-K2.5-fp4` is **non-serverless**
> (live: 400 `model_not_available`) and would need a dedicated Together endpoint (per-minute
> GPU pricing), so it's parked as `enabled=False`. `moonshotai/Kimi-K2.6` is a live-confirmed
> serverless Kimi-2.5-class model with tool calls and runs on the normal `together/` provider
> with no special config.

### Running

```bash
# Primary: print the resolved plan (which models RUN / SKIP / are DISABLED), run nothing
uv run python evals/run_matrix.py --dry-run --mode all

# Full open-weights sweep, MCP mode (on-demand — NOT run in CI; costs $$ + time)
uv run python evals/run_matrix.py --tier all --mode mcp

# Cheap smoke: one model, mcp, 2 samples
uv run python evals/run_matrix.py --tier fast --mode mcp --limit 2 --models Qwen3.5-9B
```

Flags: `--tier fast|medium|high|all`, `--mode mcp|cli|combined|all`,
`--models <comma-sep substrings>` (filter), `--limit N` (cap samples),
`--max-connections N` (default 1 on a shared judge key; auto-bumps to 4 when
`EVAL_JUDGE_API_KEY` is set — an explicit value always wins),
`--judge-model <together/...>`, `--log-dir <path>`, `--dry-run`,
`--no-verify` (skip the live catalog check).

### Per-tier generation config

Each model under test is run with a per-tier `GenerateConfig` from
`mcp-common` v0.29.0 (`generate_config_for_tier(model.tier)`), which pins the
small-model reliability levers:

- **`temperature=0`** — deterministic tool selection / argument generation.
- **a tier `max_tokens` cap** — `fast` 1024 / `medium` 2048 / `high` 4096. A
  capped budget keeps latency/cost down; watch for `finish_reason == "length"`
  (a truncated completion can cut a tool call mid-JSON).
- **thinking off** — `reasoning_effort="none"` **and**
  `extra_body.chat_template_kwargs.enable_thinking=False` (belt-and-suspenders
  across providers / the Together vLLM chat template). **Together caveat:**
  Together rejects `reasoning_effort="none"` with HTTP 400 ("Input validation
  error") for several served models (live-probed: `Qwen3.5-9B`, `gpt-oss-20b`),
  so for Together-routed models the runner drops `reasoning_effort` and relies
  on the `extra_body` chat-template switch (which Together honors) for
  thinking-off. Non-Together providers keep `reasoning_effort`.

These reach the model via `inspect_ai.eval()`'s generation kwargs: inspect
0.3.211 accepts `GenerateConfig` fields as `**kwargs` (it builds
`GenerateConfig(**kwargs)` internally) while `model_args` is a *separate*
parameter — so the per-tier config never clobbers the streaming `model_args`
wiring. The applied levers are printed per eval and recorded under
`results[].generate_config` in `summary.json`.

### Serial execution & the judge

- **Fixed judge.** Scoring is comparable across models because the judge is fixed to
  `JUDGE_MODEL` (`together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput`). The judge is separate
  from the model under test: `mcp_common`'s scorers read the bare slug from
  `EVAL_JUDGE_MODEL` (which the runner exports automatically) and resolve the judge client
  independently — `EVAL_JUDGE_API_KEY` if set (else `TOGETHER_API_KEY`) and
  `EVAL_JUDGE_BASE_URL` if set (else the default Together endpoint) (mcp-common #132).
- **Serial by default on a shared key.** When the judge shares `TOGETHER_API_KEY` with the
  model under test, Together rate-limits (429) under concurrency, so the runner defaults
  `--max-connections 1` and runs one eval per `(model, mode)` at a time.
- **Auto-bump when the judge is decoupled.** Set `EVAL_JUDGE_API_KEY` (a separate judge
  key/budget) and the default `--max-connections` auto-bumps to **4** — the judge no longer
  competes with the model-under-test budget, so a modest bump is safe. The chosen value and
  the reason are printed in the run header and saved to `summary.json`
  (`max_connections`, `max_connections_reason`, `judge_decoupled`).
- **Explicit override always wins.** Passing `--max-connections N` uses `N` regardless of
  the env (raise it further on a dedicated judge, or force `1` to stay serial). Do **not**
  raise it on a shared key — that re-triggers the judge 429 stall (netbox-mcp#121).

### Caveats

- **Closed models are gated by env keys.** `gpt-5.5` / `claude-opus-4-8` are skipped with a
  clear message unless `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are set (only `TOGETHER_API_KEY`
  is required for the open-weights tiers + the judge).
- **`composer-2.5` is deferred.** Cursor's SDK is agentic, not an inspect chat provider, so
  it needs a custom bridge; it's parked as `enabled=False` in the registry.
- **Catalog listing is advisory, not a gate.** When `TOGETHER_API_KEY` is set, the runner
  fetches the live catalog to sanity-check slugs, but catalog *listing/pricing is an
  unreliable serverless signal* — many listed models still 400 as non-serverless, and some
  servable models are absent or served under versioned ids. So a slug missing from the listing
  produces a **warning and the model still runs** (surfaced in the plan output and
  `summary.json`) rather than a hard skip. Pass `--no-verify` to skip the catalog fetch
  entirely. (A live invocation probe is the only reliable serverless test.)
- **The full sweep is on-demand**, not part of CI.

### Logs

Each run's inspect log lands in `--log-dir` (default `evals/logs/matrix/<timestamp>/`, which is
git-ignored), alongside a `summary.json`. At the end the runner prints a comparison table:
`model × mode → accuracy / CORRECT-PARTIAL-INCORRECT counts`. Open any run in the inspect
viewer with `inspect view --log-dir evals/logs/matrix/<timestamp>`.

### Preserved results

Durable, committed run summaries live in [`evals/results/<date>/`](results/) — a human-readable
`RESULTS.md` comparison table, the machine-readable merged `summary.json`, and `per_scenario.csv`.
(Raw `.eval` logs stay local/git-ignored under `evals/logs/`; browse them with `uv run inspect view`.)

### Trend history & nightly smoke (#88 Phase 3b/3c)

Append each run's `summary.json` to an append-only `history.jsonl` and render a
release-over-release trend (off by default — CI does not append):

```bash
uv run python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
    --history evals/results/history.jsonl --trend-dir evals/results/trend
```

`--trend-dir` writes `trend.md` (Markdown table + Mermaid `xychart`, both
GitHub-inline) and `sections.json`. See `docs/EVALS.md` for the full guide.

The recommended **nightly smoke** (cheap, on-demand — not a scheduled CI
workflow, to avoid unattended model spend) is the command above:
`--tier fast --mode mcp --limit 5`. Run it by hand or from your own scheduler.

## Scenarios

Scenarios are defined in `scenarios.json` using the `mcp-common` `Scenario` model. Each scenario has:

| Field | Description |
|-------|-------------|
| `input` | The prompt given to the agent |
| `expected_tools` | Tool names the agent should call |
| `expected_behavior` | Natural-language description for LLM-as-judge |
| `mode` | Which eval modes the scenario applies to: `mcp`, `cli`, or `both` |
| `tags` | Categorization tags for filtering and reporting |

### Current Scenarios

17 scenarios total (10 original + 7 read-only pathways validated in a live wet-run).

| # | Category | Mode | Scenario |
|---|----------|------|----------|
| 1 | Happy path | both | Device status + vendor (Provider_Machine_ID) lookup |
| 2 | Filtering / precision | both | Active device count in a cluster (cluster_id, not text) |
| 3 | Error handling | both | Non-existent device lookup |
| 4 | Multi-step | both | Extract cluster + primary IP from device |
| 5 | Tool selection | mcp | BMC IP via NetBox (not Redfish) |
| 6 | Vendor lookup | both | Vendor name → Together hostname |
| 7 | Ambiguity | both | Unscoped vendor-name lookup (site ambiguous) |
| 8 | Filtering / negative | both | Cluster devices that are NOT active |
| 9 | IP resolution | both | All IPs (in-band + OOB) for a device |
| 10 | CLI-specific | cli | `netbox-cli` site-scoped active-device list |
| 11 | Provider machine ID | both | Provider machine ID → device (cf_Provider_Machine_ID fallback) |
| 12 | IP resolution | both | Primary IP → device (reverse resolution) |
| 13 | OOB / BMC | both | OOB/BMC summary tool (`DeviceOOBSummary`) |
| 14 | Batch / multi-ID | both | Batch-fetch devices by IDs in one call |
| 15 | Lookup by ID | both | Get a device by numeric ID |
| 16 | Large result / pagination | both | Large-cluster enumeration + full count (~476) |
| 17 | Ambiguity | both | Ambiguous hostname → multiple devices across sites |

## Adding New Scenarios

1. Add an entry to `scenarios.json` following the `Scenario` schema
2. Set `mode` to control which eval tasks pick it up
3. Use `tags` for categorization (e.g. `happy_path`, `error_handling`, `write_operation`)
4. Run the relevant eval to verify

```json
{
  "input": "Your prompt to the agent",
  "expected_tools": ["netbox_lookup_device"],
  "expected_behavior": "What the agent should do",
  "mode": "both",
  "tags": ["your_category"]
}
```

## Interpreting Results

Inspect AI produces a log file with per-scenario scores:

- **Tool selection** (deterministic): Did the agent call the expected tools?
- **Task completion** (LLM judge): Did the agent's response satisfy the request?
- **Interface choice** (combined mode only): Did the agent prefer CLI over MCP?

Overall classification:
- `CORRECT` — tool selection ≥ 0.8 and task completion ≥ 0.7
- `PARTIAL` — either score ≥ 0.5
- `INCORRECT` — both scores below thresholds

View results with:
```bash
inspect view
```

## Scoring

Scorers are defined in `mcp-common` (`mcp_common.testing.eval.scorers`):

| Scorer | Used by | Dimensions |
|--------|---------|------------|
| `tool_use_scorer` | MCP | Tool selection (MCP tool names) + task completion |
| `cli_tool_use_scorer` | CLI, Combined | CLI-aware tool selection (parses `netbox-cli <subcommand>`; with `accept_mcp_names` also credits direct MCP tool calls) + task completion |
| `parity_scorer` | (future) | Cross-mode result equivalence |
