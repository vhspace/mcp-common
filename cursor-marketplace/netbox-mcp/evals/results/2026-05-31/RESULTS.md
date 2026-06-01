# netbox-mcp eval matrix — preserved results

**Run date (UTC):** 2026-05-31  
**netbox-mcp:** v2.21.0  |  **mcp-common:** v0.29.0  
**Judge model (LLM-as-judge):** `claude-sonnet-4-6` (**Anthropic**, OpenAI-compatible endpoint `https://api.anthropic.com/v1/`)  
**Judge decoupling:** judge runs on a **separate Anthropic key/budget** via `EVAL_JUDGE_API_KEY` / `EVAL_JUDGE_BASE_URL` / `EVAL_JUDGE_MODEL` (mcp-common #132); it does **not** share the model-under-test's Together budget.  
**Max connections:** 4 (auto-bumped from the serial default because the judge is decoupled — mcp-common #133 / `resolve_max_connections`).  
**Scenarios:** mcp mode = 16, cli mode = 16 (mode-filtered per scenario).  
**Models under test:** the same **open-weights Together** set as #121 (the Anthropic key was used **only** for the judge — `anthropic/claude-opus-4-8` and `openai/gpt-5.5` stayed SKIPPED as models-under-test), plus the gpt-oss baselines (#123) and the serverless Kimi-K2.6 (#115) that landed after #121.  
**NetBox access:** READ-ONLY (no mutations).  
**Cost / runtime:** ~3.86M model-under-test tokens (Together credits) across **16/16 successful evals** (8 models × mcp+cli, **0 errors**); ~75 min of eval compute (mcp ~20 min + cli ~55 min). Judge tokens are billed **separately** on the Anthropic key and are not counted here.

## Model comparison (mcp vs cli)

| Model | Tier | mcp acc | mcp C/P/I | cli acc | cli C/P/I | mcp task-compl | cli task-compl |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | fast | 0.906 | 13/3/0 | 0.656 | 6/9/1 | 0.900 | 0.413 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | fast | 0.844 | 11/5/0 | 0.188 | 0/6/10 | 0.812 | 0.169 |
| `openai/gpt-oss-20b` | fast | 0.875 | 12/4/0 | 0.531 | 1/15/0 | 0.794 | 0.088 |
| `openai/gpt-oss-120b` | fast | 0.844 | 11/5/0 | 0.562 | 2/14/0 | 0.731 | 0.169 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | medium | 0.938 | 14/2/0 | 0.938 | 14/2/0 | 0.941 | 0.938 |
| `deepseek-ai/DeepSeek-V4-Pro` | medium | 0.906 | 13/3/0 | 0.938 | 14/2/0 | 0.931 | 0.903 |
| `MiniMaxAI/MiniMax-M2.7` | medium | 0.938 | 14/2/0 | 0.969 | 15/1/0 | 0.950 | 0.934 |
| `moonshotai/Kimi-K2.6` | high | 0.906 | 13/3/0 | 0.969 | 15/1/0 | 0.931 | 0.969 |

> **C/P/I** = CORRECT / PARTIAL / INCORRECT sample counts. **acc** = inspect `accuracy` = (CORRECT + 0.5·PARTIAL) / N (matches the runner's table). **task-compl** = mean LLM-judge (Anthropic) task-completion score (0–1) — the fair cross-mode signal (it isolates *did the agent answer the question* from the CLI tool-selection mechanics).

## Comparison vs #121 (the run this supersedes)

#121 (`chore/refresh-eval-results`, 2026-05-30) ran the matrix with the **Together** judge (`Qwen/Qwen3-235B-A22B-Instruct-2507-tput`) at `--max-connections 1` on netbox-mcp v2.17.0 / mcp-common v0.27.0. Its **cli sweep stalled** (only 3/7 models completed; DeepSeek-V4-Pro died at 10/16; MiniMax-M2.7, Qwen3.7-Max, Kimi-K2.6 never ran; **no cli `summary.json` was ever written**) — Together rate-limiting on the shared judge key (#120). #121 also recorded `Qwen3.7-Max` mcp = **0.000** (broken streaming route).

This run **fixes the structural failures**:

| Aspect | #121 (2026-05-30) | This run (2026-05-31) |
|---|---|---|
| Judge | Together `Qwen3-235B` (shared budget) | **Anthropic `claude-sonnet-4-6`** (separate budget) |
| max_connections | 1 (serial, forced by judge 429s) | **4** (auto-bump; judge decoupled) |
| mcp sweep | 7/7 complete (1 broken: Qwen3.7-Max 0.000) | **8/8 complete, 0 errors** |
| cli sweep | **PARTIAL 3/7** (stalled, no summary.json) | **COMPLETE 8/8**, summary.json written |
| Broken high-tier | Qwen3.7-Max mcp 0.000 (empty streaming) | gated/disabled (#124); replaced by working Kimi-K2.6 |

**Per-model accuracy deltas vs #121** (same model, mcp / cli; ⚠ = judge changed Together→Anthropic, so absolute deltas conflate harness *and* judge):

| Model | #121 mcp → now | #121 cli → now |
|---|---|---|
| `Qwen3.5-9B` | 0.781 → **0.906** | 0.062 → **0.656** |
| `Llama-3.3-70B` | 0.719 → **0.844** | 0.438 → **0.188** (regression) |
| `Qwen3-235B` | 0.906 → **0.938** | 0.875 → **0.938** |
| `DeepSeek-V4-Pro` | 0.938 → **0.906** | _partial 10/16_ → **0.938** (now complete) |
| `MiniMax-M2.7` | 0.906 → **0.938** | _not run_ → **0.969** (now complete) |
| `Kimi-K2.6` | 0.906 → **0.906** | _not run_ → **0.969** (now complete) |
| `gpt-oss-20b` / `gpt-oss-120b` | _added post-#121 (#123)_ | _added post-#121 (#123)_ |
| `Qwen3.7-Max` | 0.000 (broken) | _not run_ → **gated** (#124, not run) |

**Harness improvements landed since #121** (origin/main): #112 (tool descriptions / prompt), #122 (eval tool-trim), #125 (adopt CLI aliases → `tool_subcommands`), #130/#131 (hardening), #133 (v0.29.x reliability helpers + judge-decoupling auto-bump consuming mcp-common #132). netbox-mcp v2.17.0 → **v2.21.0**, mcp-common v0.27.0 → **v0.29.0**.

## Takeaways

- **The cli sweep COMPLETED this time (8/8).** Moving the judge to its own Anthropic key/budget removed the shared-Together-judge 429 contention (#120/#132) that stalled #121's cli run after DeepSeek. Only **3 transient 429s** occurred across the whole cli sweep (all auto-recovered by inspect's retry); none stalled the run.
- **MCP mode is strong and uniform:** every model lands **0.844–0.938** acc with **0 INCORRECT** samples — the small `Qwen3.5-9B` (0.906) is now nearly on par with the frontier open-weights models, a large jump from #121's 0.781 (harness improvements + a more discriminating judge).
- **CLI mode now separates the field clearly:** the strong models (`Qwen3-235B` 0.938, `DeepSeek-V4-Pro` 0.938, `MiniMax-M2.7` 0.969, `Kimi-K2.6` 0.969) match or beat their mcp accuracy, while the small/`gpt-oss` models drop. The CLI-aware scorer + aliases (#125 / mcp-common #133) credit `netbox-cli` subcommands, so CLI accuracy is no longer structurally ~0 (`Qwen3.5-9B` cli 0.062 → 0.656).
- **`gpt-oss-20b/120b` are good MCP tool-callers but weak CLI agents:** high mcp acc (0.875 / 0.844) but very low cli **task-completion** (0.088 / 0.169) — they run the right `netbox-cli` subcommand (earning PARTIAL on tool-selection) but don't synthesize the correct answer from its output.
- **`Llama-3.3-70B` cli regressed (0.438 → 0.188, 0/6/10):** it is the one model that got materially *worse* in cli. Its mcp task-completion (0.812) is fine, so this is a CLI-workflow weakness, amplified by the stricter Anthropic judge. Worth a closer look, but it is a genuine model result, not an infra failure.
- **Read-only held:** all NetBox access was read-only (no mutations); the parent-resolved plain `NETBOX_TOKEN` preflight (GET `/api/status/`) passed before each sweep.

## Run plan, skips & failures

- **RAN:** 8 open-weights Together models × 2 modes (mcp, cli) = **16 evals, all `success`, 0 errors.**
- **SKIPPED (as models-under-test):** `openai/gpt-5.5` (`OPENAI_API_KEY` not set), `anthropic/claude-opus-4-8` (`ANTHROPIC_API_KEY` not set). The Anthropic key was supplied **only** as the judge key (`EVAL_JUDGE_API_KEY`), so the frontier closed models were correctly **not** added to the run set — the open-weights set matches #121.
- **DISABLED:** `openai-api/together/Qwen/Qwen3.7-Max` (GATED #124 — Together thinking-mode streaming returns `finish_reason=tool_calls` but zero deltas → empty completion; was the 0.000 in #121), `together/moonshotai/Kimi-K2.5-fp4` (non-serverless; replaced by the serverless `Kimi-K2.6`), `cursor/composer-2.5` (needs an agentic bridge).
- **Eval-level failures:** none.

## Judge configuration (reproducibility)

- The judge client (mcp-common `scorers._get_llm_client`) reads `EVAL_JUDGE_API_KEY` → `EVAL_JUDGE_BASE_URL` → `EVAL_JUDGE_MODEL`. Set: key = the Anthropic key, base URL = `https://api.anthropic.com/v1/`, model = `claude-sonnet-4-6` (passed via `run_matrix.py --judge-model claude-sonnet-4-6`; `judge_api_string` leaves the bare Claude id untouched since there is no `together/` prefix to strip).
- **One shim was required:** mcp-common v0.29.0 hardcodes `response_format={"type":"json_object"}`, which Anthropic's OpenAI-compatible endpoint rejects (`400 — response_format.type: Input should be 'json_schema'`). The judge prompts already instruct *"respond with ONLY a JSON object"*, and Claude (temperature 0) honors that, so the run dropped `response_format` for the judge call (runtime patch of `scorers._call_llm_judge`; no committed harness change). Verified end-to-end before the full run (a tiny mcp/limit-1 check returned a real `task_completion=1.0` with a Claude-authored rationale). **Follow-up:** make mcp-common's judge `response_format` provider-aware so a native Anthropic judge needs no shim (refs mcp-common #132).
- The runner prints `[WARN: judge always runs on Together regardless of prefix]` — this is a **stale cosmetic warning**; the `judge endpoint` line (`EVAL_JUDGE_BASE_URL=https://api.anthropic.com/v1/`) is authoritative and the judge demonstrably ran on Anthropic.

## Raw logs (local, git-ignored)

Full inspect `.eval` logs (per-sample transcripts, tool calls, judge rationale) are **not committed** (large; `evals/logs/` is git-ignored). They live locally at:

- `evals/logs/matrix/2026-05-31-mcp` (mcp)
- `evals/logs/matrix/2026-05-31-cli` (cli)

Browse them with the inspect viewer:

```bash
uv run inspect view --log-dir evals/logs/matrix/2026-05-31-mcp
uv run inspect view --log-dir evals/logs/matrix/2026-05-31-cli
```
