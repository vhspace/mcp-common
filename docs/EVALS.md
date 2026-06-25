# Eval & description-QA guide

How the togethercomputer MCPs are evaluated and quality-gated. Covers the
four-tier testing pyramid's top two tiers — the LLM-as-judge eval suite
(`mcp_common.testing.eval`, the `[eval]` extra) and the heuristic
description-QA CI gate (#88).

> See `docs/AGENT_CONVENTIONS.md` for the foundation libraries every server
> shares; this doc is the eval-specific companion.

## TL;DR

- **CI gate (every PR, no tokens):** `python -m mcp_common.testing.eval
  description-qa --server <module> [--server ...]` — heuristic tool-description
  quality + inter-server collision check. Wired in
  `.github/workflows/description-qa.yml`.
- **On-demand eval (LLM-as-judge, costs $$):** each server's
  `evals/run_matrix.py --tier fast --mode mcp --limit 5`. Not in CI.
- **Trend history (off by default):** `run_matrix.py --history
  evals/results/history.jsonl --trend-dir evals/results/trend` appends each run
  and renders a release-over-release trend.

## The description-QA CI gate (#88 Phase 3a)

A fast, token-free gate that runs on every PR. It checks the MCP
tool **descriptions** an agent sees — the text in the system prompt that drives
tool selection — for two classes of problem:

1. **Per-server heuristic issues** (`check_description_quality`):
   - `too_vague` — description under 20 chars (empty / uninformative).
   - `missing_parameters` — the tool has parameters the description never names.
   - `missing_error_info` / `missing_return_info` — no mention of failure
     behaviour / return shape.
   - `too_long` — over 500 chars (wastes system-prompt tokens).
2. **Cross-server similarity conflicts** (`check_similarity_conflicts`): pairs
   of tools on **different** servers whose descriptions are >60% similar
   (difflib `SequenceMatcher`) — an agent with both servers loaded may confuse
   them. Only run when more than one `--server` is passed.

### Running it

```bash
# Single server (per-server QA, no cross-server check)
uv run --with-editable ../.. --extra eval \
  python -m mcp_common.testing.eval description-qa --server netbox_mcp.server

# Cross-server: pass every server an agent might load together
uv run --with-editable ../.. --extra eval \
  --with-editable ../awx-mcp --with-editable ../netbox-mcp \
  python -m mcp_common.testing.eval description-qa \
  --server netbox_mcp.server \
  --server awx_mcp.server \
  --server dc_support_mcp.mcp_server

# Machine-readable report for nightly tooling
... description-qa --server <m> --json
```

Flags:
- `--server` (repeat) — dotted import path of an MCP server module.
- `--fail-on <type>` (repeat) — issue type that fails the gate; `all` widens
  to every type.
- `--strict` — shorthand for `--fail-on all` (fail on every issue + conflicts).
- `--no-similarity` — skip the cross-server conflict check.
- `--json` — emit a JSON report instead of text.

### What fails the gate

The default failing set is the **clear-correctness** signals — `too_vague`
(empty/tiny descriptions) and cross-server collisions — so the gate is green
on a healthy repo and trips on real regressions. The softer heuristics
(`missing_error_info` / `missing_return_info` / `too_long` /
`missing_parameters`) are reported as **advisory** (they surface pre-existing
description gaps without blocking every PR). Pass `--strict` to fail on them
too — the right lever once a server's descriptions are cleaned up.

Exit code is `1` when any failing-set issue is found, else `0`.

### CI wiring

`.github/workflows/description-qa.yml` runs two jobs on every PR/push to
`main`/`dev`:

- `description-qa` — a matrix over `netbox-mcp`, `awx-mcp`, `dc-support-mcp`,
  each running the per-server gate in that server's venv.
- `description-qa-cross` — one job that imports all three servers in one
  process (via chained `--with-editable`) and runs the cross-server collision
  check.

Each server is an independent uv project that pins `mcp-common` separately, so
the gate runs each server against the **in-repo** `mcp-common` HEAD via
`--with-editable ../..` (the same HEAD-canary trick as `canary.yml`) — the gate
tests the library under review, not each server's pinned (possibly older) tag.

> **netbox-mcp note:** netbox pins `mcp-common` by git rev. The
> `--with-editable ../..` override makes the gate use the in-repo library, so
> netbox joins the gate today; no re-pin needed for the gate to run.

## Per-server trend history (#88 Phase 3b)

Each server's `run_matrix.py` can append every run's `summary.json` to an
append-only `history.jsonl` and render a release-over-release trend. It is
**off by default** (CI does not append; evals are on-demand) — opt in with
`--history`:

```bash
uv run python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
    --history evals/results/history.jsonl \
    --trend-dir evals/results/trend
```

`--trend-dir` renders `trend.md` (a Markdown comparison table + a Mermaid
`xychart-beta` headline line — both render inline on GitHub with no hosting)
and `sections.json` (a viz-mcp spec; the interactive Plotly HTML/PNG render
too, **iff viz-mcp is installed**). The helpers are
`mcp_common.testing.eval.report.append_history` / `render_trend`.

Durable, committed run snapshots live under `evals/results/<date>/`
(`RESULTS.md`, `summary.json`, `per_scenario.csv`) — the netbox-mcp convention;
awx-mcp and dc-support-mcp use the same layout. Raw `.eval` logs stay
local/git-ignored under `evals/logs/`.

## Optional nightly smoke (#88 Phase 3c)

A cheap, on-demand smoke run per server — one fast model, MCP mode, 5 samples —
to catch regressions between full sweeps. **Not wired as a scheduled workflow**
(KISS: evals need live credentials + spend budget that doesn't belong in
unattended CI); run it by hand or from your own scheduler:

```bash
# netbox-mcp
cd servers/netbox-mcp
uv run --with-editable ../.. --extra eval \
  python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
  --history evals/results/history.jsonl --trend-dir evals/results/trend

# awx-mcp (needs MCP_ENFORCE_READONLY=1 + AWX_TOKEN)
cd servers/awx-mcp
MCP_ENFORCE_READONLY=1 uv run --with-editable ../.. --extra eval \
  python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
  --history evals/results/history.jsonl --trend-dir evals/results/trend

# dc-support-mcp (needs MCP_ENFORCE_READONLY=1 + a vendor credential)
cd servers/dc-support-mcp
MCP_ENFORCE_READONLY=1 uv run --with-editable ../.. --extra eval \
  python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
  --history evals/results/history.jsonl --trend-dir evals/results/trend
```

If you want a scheduled GitHub Actions nightly, the pattern is a
`workflow_dispatch` + `schedule:` workflow that runs the command above per
server with the server's credentials in secrets. We deliberately don't ship
one to avoid unattended model spend; each server's `evals/README.md` records
the recommended command.

## Where to look

- `src/mcp_common/testing/eval/description_qa.py` — heuristic checks + the
  `description-qa` CLI (`run_description_qa`, `qa_main`, `qa_app`).
- `src/mcp_common/testing/eval/report.py` — `append_history`, `render_trend`,
  and the legacy `report` CLI.
- `src/mcp_common/testing/eval/matrix_runner.py` — the shared matrix runner
  (`--history` / `--trend-dir` plumbing).
- `servers/*/evals/run_matrix.py` — each server's thin wrapper.
- `.github/workflows/description-qa.yml` — the CI gate.
