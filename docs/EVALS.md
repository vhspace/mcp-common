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
- **MCP ↔ CLI parity (on-demand, #88 Phase 4a):** `python -m
  mcp_common.testing.eval parity --reference <mcp.eval> --candidate <cli.eval>`
  compares two run logs for response equivalence. Not in CI.
- **DeepEval on failures (on-demand, #88 Phase 4b):** `python -m
  mcp_common.testing.eval deepeval-failures --source <logs/>` runs
  faithfulness/hallucination on the INCORRECT/PARTIAL samples only. Not in CI;
  requires the `[eval-scoring]` extra.

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

## MCP ↔ CLI parity regression (#88 Phase 4a)

`parity_scorer` (in `scorers.py`) compares a live sample against a *reference
JSON-lines* log captured out of band. The **post-run** parity helper
(`mcp_common.testing.eval.parity`) is the matching capability for already-written
Inspect `.eval` logs: it pairs samples from an MCP-mode run and a CLI-mode run
of the same scenario set by their input text, and asks the shared LLM-as-judge
(the same `EVAL_JUDGE_*`-aware client + parity prompt the scorer uses) whether
each pair's responses are semantically equivalent.

**Why a post-run helper, not a `--parity` matrix mode:** a parity mode would run
every scenario twice inside one `run_matrix` call, doubling model + judge tokens
for the whole sweep. Parity is a *regression* check, not a per-PR gate — it
belongs on demand. Running two normal evals and comparing their logs keeps the
cost opt-in and the matrix untouched.

### Running it

```bash
# 1. Run the same scenario set in both modes (one fast model is enough for a
#    parity check — you're comparing interfaces, not model quality).
cd servers/netbox-mcp
uv run --with-editable ../.. --extra eval \
  python evals/run_matrix.py --tier fast --mode mcp --limit 5
uv run --with-editable ../.. --extra eval \
  python evals/run_matrix.py --tier fast --mode cli --limit 5

# 2. Compare the two run log directories.
uv run --with-editable ../.. --extra eval \
  python -m mcp_common.testing.eval parity \
    --reference evals/logs/matrix/<mcp-timestamp> \
    --candidate evals/logs/matrix/<cli-timestamp> \
    --out-dir evals/results/parity
```

Each side accepts a single `.eval` file **or** a directory of `.eval` files (a
matrix run's `logs/` dir); samples are keyed by input text, so the two runs must
share scenario inputs (the same `scenarios.json`). The judge needs
`EVAL_JUDGE_API_KEY` (preferred) or `TOGETHER_API_KEY`.

The command writes `parity.md` (a diffable Markdown table — one row per paired
input, GitHub-inline) and `parity.json` (summary + per-pair detail) to
`--out-dir` (default: the candidate's directory).

### What the report shows

- **paired / judged / equivalent / parity rate** — coverage and the equivalence
  rate over the judged pairs. A pair is *equivalent* when the judge score is
  `>= 0.8` (the `parity_scorer` CORRECT threshold).
- **coverage drift** — inputs that ran in only one mode (a scenario that didn't
  run in both), a coverage signal, not a parity failure.
- **skipped pairs** — a side that produced no response (empty transcript /
  crash) is not judged; that's a harness failure, not a parity failure.

Helpers: `mcp_common.testing.eval.compare_eval_logs` /
`compare_logs`, `summarize_parity`, `build_parity_markdown`,
`load_samples_by_input`.

## DeepEval on failures only (#88 Phase 4b)

The DeepEval quality scorers (`faithfulness_scorer` / `hallucination_scorer` in
`scorers.py`) judge the natural-language response against the tool outputs. They
are expensive (one or more judge calls per sample), and most samples *pass* —
their quality is implied by the structural score — so attaching them to every
sample roughly doubles an eval sweep's judge cost for little signal.

The **post-run** DeepEval-on-failures hook
(`mcp_common.testing.eval.deepeval_on_failures`) is the cost-aware counterpart:
it takes a results directory (or `.eval` logs) the matrix already produced,
filters to the samples scored **INCORRECT / PARTIAL** by the existing analyzer
filter, and runs the DeepEval faithfulness + hallucination metrics on **just
those** — reusing the shared judge client and `deepeval_backend`. Samples with no
response or no tool outputs are skipped (nothing to quality-check), matching the
live scorers' short-circuits.

### Running it

```bash
# Requires the optional eval-scoring extra:
uv pip install "mcp-common[eval-scoring]"

cd servers/netbox-mcp
uv run --with-editable ../.. --extra eval --extra eval-scoring \
  python -m mcp_common.testing.eval deepeval-failures \
    --source evals/logs/matrix/<timestamp> \
    --out-dir evals/results/deepeval
```

The judge needs `EVAL_JUDGE_API_KEY` (preferred) or `TOGETHER_API_KEY`. The
command writes `deepeval_failures.md` (a per-failure table: faithfulness +
hallucination verdicts) and `deepeval_failures.json` (summary + per-sample
detail) to `--out-dir` (default: the source's directory). Thresholds are
tunable via `--faithfulness-threshold` / `--hallucination-threshold`
(faithfulness higher-is-better; hallucination lower-is-better).

Without the `[eval-scoring]` extra the hook exits with a clear install hint
instead of raising; the import is lazy, so importing the eval package never
requires DeepEval.

Helpers: `mcp_common.testing.eval.run_deepeval_on_failures`,
`collect_failure_samples`, `summarize_deepeval_failures`,
`build_deepeval_failure_markdown`.

## Where to look

- `src/mcp_common/testing/eval/description_qa.py` — heuristic checks + the
  `description-qa` CLI (`run_description_qa`, `qa_main`, `qa_app`).
- `src/mcp_common/testing/eval/report.py` — `append_history`, `render_trend`,
  and the legacy `report` CLI.
- `src/mcp_common/testing/eval/matrix_runner.py` — the shared matrix runner
  (`--history` / `--trend-dir` plumbing).
- `src/mcp_common/testing/eval/parity.py` — MCP ↔ CLI parity log comparison
  (#88 Phase 4a) + the `parity` CLI.
- `src/mcp_common/testing/eval/deepeval_on_failures.py` — post-hoc DeepEval on
  INCORRECT/PARTIAL samples (#88 Phase 4b) + the `deepeval-failures` CLI.
- `src/mcp_common/testing/eval/__main__.py` — subcommand dispatch
  (`description-qa` / `parity` / `deepeval-failures`, else the legacy `report`).
- `servers/*/evals/run_matrix.py` — each server's thin wrapper.
- `.github/workflows/description-qa.yml` — the CI gate.
