# netbox-mcp eval matrix — preserved results (cli re-baseline)

**Run date (UTC):** 2026-06-01  
**netbox-mcp (under test):** v2.21.1  |  **mcp-common:** v0.29.0  
**Judge model (LLM-as-judge):** `claude-sonnet-4-6` (**Anthropic**, OpenAI-compatible endpoint `https://api.anthropic.com/v1/`)  
**Max connections:** 4 (auto-bumped; judge decoupled).  
**Supersedes #137's cli column:** #137's cli sweep ran a **stale global `netbox-cli` v2.14.1** (pre-#125 `lookup`->`lookup-device` rename) via `$PATH` AND used the interactive `bash_session` tool. This run uses the **repo's current build** (version preflight–verified) + one-shot `bash` (#139). mcp is re-run for an apples-to-apples pairing.  
**Version preflight:** OK — resolved netbox-cli v2.21.1 and netbox-mcp v2.21.1 from the repo venv bin (`/workspaces/together/netbox-mcp-rebaseline/.venv/bin`); both == pyproject 2.21.1.  
**NetBox access:** READ-ONLY (no mutations).  
**Cost / runtime:** ~3.75M model-under-test tokens across **16 successful evals**; ~45 min of eval compute (judge tokens billed separately on the Anthropic key).

## Model comparison (mcp vs cli)

| Model | Tier | mcp acc | mcp C/P/I | cli acc | cli C/P/I | mcp task-compl | cli task-compl |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | fast | 0.906 | 13/3/0 | 0.531 | 5/7/4 | 0.912 | 0.453 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | fast | 0.844 | 11/5/0 | 0.531 | 1/15/0 | 0.769 | 0.175 |
| `openai/gpt-oss-20b` | fast | 0.812 | 10/6/0 | 0.531 | 2/13/1 | 0.644 | 0.237 |
| `openai/gpt-oss-120b` | fast | 0.719 | 7/9/0 | 0.688 | 6/10/0 | 0.531 | 0.412 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | medium | 0.938 | 14/2/0 | 0.906 | 13/3/0 | 0.938 | 0.869 |
| `deepseek-ai/DeepSeek-V4-Pro` | medium | 0.938 | 14/2/0 | 0.969 | 15/1/0 | 0.956 | 0.950 |
| `MiniMaxAI/MiniMax-M2.7` | medium | 0.938 | 14/2/0 | 0.906 | 13/3/0 | 0.956 | 0.863 |
| `moonshotai/Kimi-K2.6` | high | 0.938 | 14/2/0 | 0.938 | 14/2/0 | 0.956 | 0.928 |

> **C/P/I** = CORRECT/PARTIAL/INCORRECT counts. **acc** = (C + 0.5·P)/N. **task-compl** = mean LLM-judge (Anthropic) task-completion (0–1).

## CLI re-baseline vs #137 (the stale-binary cli column this supersedes)

| Model | #137 cli acc | now cli acc | Δ | #137 cli C/P/I | now cli C/P/I | #137 cli task-compl | now cli task-compl |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | 0.656 | 0.531 | -0.125 | 6/9/1 | 5/7/4 | 0.412 | 0.453 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 0.188 | 0.531 | +0.344 | 0/6/10 | 1/15/0 | 0.169 | 0.175 |
| `openai/gpt-oss-20b` | 0.531 | 0.531 | +0.000 | 1/15/0 | 2/13/1 | 0.087 | 0.237 |
| `openai/gpt-oss-120b` | 0.562 | 0.688 | +0.125 | 2/14/0 | 6/10/0 | 0.169 | 0.412 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | 0.938 | 0.906 | -0.031 | 14/2/0 | 13/3/0 | 0.938 | 0.869 |
| `deepseek-ai/DeepSeek-V4-Pro` | 0.938 | 0.969 | +0.031 | 14/2/0 | 15/1/0 | 0.903 | 0.950 |
| `MiniMaxAI/MiniMax-M2.7` | 0.969 | 0.906 | -0.062 | 15/1/0 | 13/3/0 | 0.934 | 0.863 |
| `moonshotai/Kimi-K2.6` | 0.969 | 0.938 | -0.031 | 15/1/0 | 14/2/0 | 0.969 | 0.928 |

> **Read acc together with task-compl.** In CLI mode, `acc` blends a
> deterministic CLI tool-selection score with the LLM-judge task-completion.
> #137's stale `netbox-cli` v2.14.1 still earned tool-selection credit for the
> *text* `netbox-cli lookup-device …` even though that subcommand didn't exist
> in 2.14.1 (it errored), so #137's CLI tool-selection was artificially propped
> up while real answers were missing. With the repo build the commands actually
> run, so **task-completion is the fairer cross-run signal.**

## Takeaways

- **The version preflight did its job.** It first caught a real bug in this very
  change (an over-eager `.resolve()` pointed at the uv interpreter bin), then,
  once fixed, asserted on **both** sweeps that the resolved `netbox-cli` and the
  spawned `netbox-mcp` were the repo's current **v2.21.1** build from the project
  venv bin — not the stale `/usr/local/bin/netbox-cli` **v2.14.1** that depressed
  #137. The fact is recorded under `summary.json["version_preflight"]`.
- **CLI completed 8/8 (0 errors)**, matching mcp 8/8 — no judge-429 stall (the
  decoupled Anthropic judge + `max_connections=4`).
- **Llama-3.3-70B CLI recovered 0.188 → 0.531 (+0.344):** its **10 INCORRECT
  collapsed to 0** (0/6/10 → 1/15/0). This is the clearest artifact correction —
  #137's Llama CLI was wrecked by the combination of the stale binary and the
  interactive `bash_session` (it hallucinated a `netbox-cli` tool / never read
  session output, #138); the repo build + one-shot `bash` (#139) fixes it.
- **Fast-tier task-completion rose materially** — the honest "did it answer"
  signal: `gpt-oss-120b` **0.169 → 0.412 (+0.243)**, `gpt-oss-20b`
  **0.087 → 0.237 (+0.150)**, `Qwen3.5-9B` **0.412 → 0.453**. Weak models now run
  *working* `netbox-cli` commands and earn real/partial answers instead of
  erroring against a stale binary. `gpt-oss-120b` acc rose +0.125 (2/14/0 →
  6/10/0, +4 CORRECT).
- **`Qwen3.5-9B` CLI acc dipped 0.656 → 0.531** even though its task-completion
  rose (+0.041): with a working binary, genuinely wrong subcommand choices now
  produce clearly-wrong answers (4 INCORRECT) instead of stale-binary errors that
  still drew tool-selection credit. This is the artifact-correction surfacing a
  true CLI weakness, not a regression of the harness.
- **Strong medium/high models are flat within judge noise** (±0.03–0.06:
  `Qwen3-235B` 0.938→0.906, `DeepSeek-V4-Pro` 0.938→**0.969**, `MiniMax-M2.7`
  0.969→0.906, `Kimi-K2.6` 0.969→0.938). They were already CLI-robust, so the
  binary fix neither helps nor hurts them beyond sampling/judge variance.
- **MCP mode is unchanged-in-spirit and strong** (0.719–0.938, **0 INCORRECT**
  across all 8 models), confirming the absolute-path `mcp_server_stdio(command=…)`
  change didn't disturb the MCP column.
- **Read-only held:** every NetBox access was read-only; the parent-resolved
  plain-`NETBOX_TOKEN` preflight (GET `/api/status/`) passed before each sweep.

## Run plan, skips & failures

- **RAN:** the same **8 open-weights Together** models as #137 × 2 modes (mcp,
  cli) = **16 evals, all `success`, 0 errors.**
- **SKIPPED (as models-under-test):** `openai/gpt-5.5` (`OPENAI_API_KEY` not set)
  and `anthropic/claude-opus-4-8` (`ANTHROPIC_API_KEY` not set). The Anthropic key
  was supplied **only** as the judge key (`EVAL_JUDGE_API_KEY`), so the frontier
  closed models stayed non-test models — the open-weights set matches #137.
- **DISABLED:** `Qwen3.7-Max` (GATED #124), `Kimi-K2.5-fp4` (non-serverless),
  `cursor/composer-2.5` (needs an agentic bridge).
- **Eval-level failures:** none.

## Judge configuration (reproducibility)

- Judge client (`mcp_common` `scorers._get_llm_client`) read `EVAL_JUDGE_API_KEY`
  (the Anthropic key, separate budget) → `EVAL_JUDGE_BASE_URL`
  (`https://api.anthropic.com/v1/`) → `EVAL_JUDGE_MODEL` (`claude-sonnet-4-6`, set
  by `run_matrix.py --judge-model`).
- **Same `response_format` shim as #137 (runtime, not a committed harness
  change):** mcp-common v0.29.0 hardcodes `response_format={"type":"json_object"}`,
  which Anthropic's OpenAI-compatible endpoint rejects (`400 — response_format.type
  must be 'json_schema'`). The judge prompt already asks for ONLY a JSON object and
  Claude (temperature 0) complies, so the run dropped `response_format` for the
  judge call by replacing `scorers._call_llm_judge` at runtime. **Follow-up:** make
  mcp-common's judge `response_format` provider-aware (refs mcp-common #132).
- The runner's `[WARN: judge always runs on Together regardless of prefix]` is a
  stale cosmetic warning; the `judge endpoint` line (`EVAL_JUDGE_BASE_URL=…
  anthropic.com…`) is authoritative.

## Raw logs (local, git-ignored)

- `evals/logs/matrix/2026-05-31-rebaseline-mcp` (mcp)
- `evals/logs/matrix/2026-05-31-rebaseline-cli` (cli)

