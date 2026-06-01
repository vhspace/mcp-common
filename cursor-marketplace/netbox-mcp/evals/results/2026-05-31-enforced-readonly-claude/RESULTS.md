# netbox-mcp eval matrix — enforced read-only run + Claude Haiku/Sonnet/Opus data

**Run date (UTC):** 2026-06-01  
**netbox-mcp (under test):** v2.24.0  |  **mcp-common:** v0.32.0  
**Enforced read-only:** **`MCP_ENFORCE_READONLY=1`** set for the whole run (ENABLED mode — blocks only `{"write"}`-tagged / `read_only=False` tools; read + unclassified tools run). **Not** `strict` (which would also block netbox's untagged read tools).  
**Judge model (LLM-as-judge):** `claude-sonnet-4-6` (**Anthropic**, OpenAI-compatible endpoint `https://api.anthropic.com/v1/`), decoupled on `EVAL_JUDGE_API_KEY`.  
**Max connections:** 4 (auto-bumped; judge decoupled).  
**Version preflight:** OK on both sweeps — resolved `netbox-cli` v2.24.0 and `netbox-mcp` v2.24.0 from the worktree venv bin (`/workspaces/together/netbox-mcp-evalrun/.venv/bin`); both == pyproject 2.24.0.  
**NetBox access:** READ-ONLY (no mutations); plain `NETBOX_TOKEN` resolved in the parent, `GET /api/status/` preflight passed before each sweep.  
**New this run:** Claude **Haiku 4.5 (fast)**, **Sonnet 4.6 (medium)**, **Opus 4.8 (high)** run as **models-under-test** (now that `ANTHROPIC_API_KEY` is present; #144). The native inspect `anthropic/` provider needs the `anthropic` SDK, so it was added to the `eval` extra (it was absent — the judge reaches Anthropic via the `openai`-compatible client, so prior runs never needed it).  
**Cost / runtime:** ~4.98M model-under-test tokens across **22 successful evals** (0 errors); ~59.5 min of summed eval compute (mcp sweep ~21 min, cli sweep ~36 min — the cli large open-weights models were heavily Together-rate-limited and self-healed via inspect's retry/backoff). Judge (Anthropic) tokens billed separately on the Anthropic key.

## Enforced read-only validation (the point of this run)

- **Reads are unaffected by `MCP_ENFORCE_READONLY=1`.** Every read-only scenario ran normally in both modes; accuracies track the [2026-05-31-rebaseline](../2026-05-31-rebaseline/RESULTS.md) within judge/sampling noise (see the delta table below), with **0 INCORRECT across all 11 models in MCP mode**. This is expected: ENABLED mode blocks only **mutating** tools (`{"write"}` tag / `read_only=False`); netbox's read tools are never blocked.
- **The #148 CLI write-gate is genuinely active in CLI mode** and was verified live: the cli `bash` sandbox inherits the parent env (incl. `MCP_ENFORCE_READONLY=1`), so `netbox-cli update-device … --confirm` is refused by the `@enforce_read_only_cli(read_only=False)` gate with exactly `This operation is not enabled.` (exit 1) **before any client is built or PATCH issued**. No eval model attempted a write, so the gate was active but never triggered during scoring (a clean tool-selection signal: all 11 models stayed read-only).
- **Caveat — MCP mode did NOT exercise the server-side middleware.** `MCP_ENFORCE_READONLY` is **not** forwarded to the spawned `netbox-mcp` stdio child: the MCP SDK gives the child only a safelist (`HOME/LOGNAME/PATH/TERM/USER`) plus `netbox_mcp_env()`'s `{NETBOX_URL, NETBOX_TOKEN}`, so in the child `current_enforce_mode()` reads the toggle as unset → `OFF`, and `ReadOnlyEnforcementMiddleware` (#148) is a pass-through. No score impact (reads pass; the write verb is already trimmed from the MCP surface by the `read_only_tools` allow-list), but the MCP-mode eval is **not** a true integration test of the server-side backstop. Filed as netbox-mcp [#146](https://github.com/vhspace/netbox-mcp/issues/146) (see *Findings → issue* below).

## Model comparison (mcp vs cli)

| Model | Tier | mcp acc | mcp C/P/I | cli acc | cli C/P/I | mcp task-compl | cli task-compl |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | fast | 0.938 | 14/2/0 | 0.625 | 7/6/3 | 0.881 | 0.606 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | fast | 0.875 | 12/4/0 | 0.531 | 1/15/0 | 0.781 | 0.144 |
| `openai/gpt-oss-20b` | fast | 0.812 | 10/6/0 | 0.594 | 3/13/0 | 0.694 | 0.244 |
| `openai/gpt-oss-120b` | fast | 0.781 | 9/7/0 | 0.688 | 6/10/0 | 0.631 | 0.469 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | medium | 0.938 | 14/2/0 | 0.938 | 14/2/0 | 0.906 | 0.912 |
| `deepseek-ai/DeepSeek-V4-Pro` | medium | 0.969 | 15/1/0 | 0.906 | 13/3/0 | 0.950 | 0.881 |
| `MiniMaxAI/MiniMax-M2.7` | medium | 0.969 | 15/1/0 | 0.906 | 13/3/0 | 0.963 | 0.834 |
| `moonshotai/Kimi-K2.6` | high | 0.969 | 15/1/0 | 0.906 | 13/3/0 | 0.956 | 0.819 |
| **`claude-haiku-4-5-20251001`** | **fast** | **0.969** | **15/1/0** | **0.906** | **13/3/0** | **0.963** | **0.900** |
| **`claude-sonnet-4-6`** | **medium** | **0.969** | **15/1/0** | **0.875** | **12/4/0** | **0.963** | **0.812** |
| **`claude-opus-4-8`** | **high** | **0.938** | **14/2/0** | **0.906** | **13/3/0** | **0.913** | **0.881** |

> **C/P/I** = CORRECT/PARTIAL/INCORRECT counts. **acc** = (C + 0.5·P)/N, N=16. **task-compl** = mean LLM-judge (Anthropic) task-completion (0–1). Model-under-test ≠ judge: the judge is a fixed, decoupled `claude-sonnet-4-6`; `claude-sonnet-4-6`-under-test is scored by it as a separate role.

## Claude (Haiku/Sonnet/Opus) vs the open-weights tiers

| Tier | Open-weights (mcp / cli) | Claude (mcp / cli) |
|---|---|---|
| **fast** | Qwen3.5-9B 0.938/0.625 · Llama-3.3-70B 0.875/0.531 · gpt-oss-20b 0.812/0.594 · gpt-oss-120b 0.781/0.688 | **Haiku 4.5 0.969 / 0.906** |
| **medium** | Qwen3-235B 0.938/0.938 · DeepSeek-V4-Pro 0.969/0.906 · MiniMax-M2.7 0.969/0.906 | **Sonnet 4.6 0.969 / 0.875** |
| **high** | Kimi-K2.6 0.969/0.906 | **Opus 4.8 0.938 / 0.906** |

- **Claude Haiku is the standout — it dominates its (fast) tier, especially in CLI mode.** At 0.969 mcp / **0.906 cli** it matches the strongest medium/high open-weights, while the other fast-tier (open-weights) models collapse in CLI: the honest task-completion signal is **Haiku 0.900** vs Llama-3.3-70B **0.144**, gpt-oss-20b **0.244**, gpt-oss-120b **0.469**, Qwen3.5-9B **0.606**. A small/cheap frontier model is far more robust at composing `netbox-cli` commands than open-weights of similar (or larger) size.
- **Claude Sonnet** ties the top open-weights medium models on MCP (0.969, 0 INCORRECT) and is a touch behind the best on CLI (0.875 vs Qwen3-235B 0.938) — still firmly in the strong band.
- **Claude Opus** lands on par with the high-tier Kimi-K2.6 (0.938/0.906).
- **MCP ≫ CLI for weak models; the gap nearly vanishes for strong models.** Claude (all three) shows the small mcp→cli drop characteristic of capable tool-callers, unlike the fast open-weights whose CLI task-completion cratered.

## vs the 2026-05-31 rebaseline (open-weights overlap; sanity that enforce mode didn't move reads)

| Model | mcp Δ (rebaseline→now) | cli Δ (rebaseline→now) |
|---|---|---|
| `Qwen/Qwen3.5-9B` | 0.906 → 0.938 (+0.032) | 0.531 → 0.625 (+0.094) |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 0.844 → 0.875 (+0.031) | 0.531 → 0.531 (0.000) |
| `openai/gpt-oss-20b` | 0.812 → 0.812 (0.000) | 0.531 → 0.594 (+0.063) |
| `openai/gpt-oss-120b` | 0.719 → 0.781 (+0.062) | 0.688 → 0.688 (0.000) |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | 0.938 → 0.938 (0.000) | 0.906 → 0.938 (+0.032) |
| `deepseek-ai/DeepSeek-V4-Pro` | 0.938 → 0.969 (+0.031) | 0.969 → 0.906 (−0.063) |
| `MiniMaxAI/MiniMax-M2.7` | 0.938 → 0.969 (+0.031) | 0.906 → 0.906 (0.000) |
| `moonshotai/Kimi-K2.6` | 0.938 → 0.969 (+0.031) | 0.938 → 0.906 (−0.032) |

> Every delta is within ±0.094 (judge/sampling noise); MCP drifted slightly **up** across the board, CLI is net-flat (mixed ±). No systematic depression — consistent with the design fact that ENABLED enforce mode is a no-op for read-only tools. **`MCP_ENFORCE_READONLY=1` does not affect the read-only scenarios.**

## Takeaways

- **Enforced read-only mode is transparent to reads.** With `MCP_ENFORCE_READONLY=1` set for the entire matrix, all 22 evals completed `success` with 0 errors and 0 INCORRECT in MCP mode; open-weights numbers match the rebaseline within noise. ENABLED mode blocks only mutating tools, so the read-only suite is unaffected — exactly as intended.
- **#148 is live in the CLI path, inert in the MCP path (harness gap).** The CLI `update-device` gate refuses writes before any PATCH (`This operation is not enabled.`, verified out-of-band). The MCP server-side middleware did **not** see the toggle, because the eval doesn't forward `MCP_ENFORCE_READONLY` to the `mcp_server_stdio` child — a one-line `netbox_mcp_env()` fix would make the MCP eval a true #148 integration test (issue filed).
- **No model attempted a write.** Across 352 read-only scenario samples (22 evals × 16) no model selected a write tool/command — a clean tool-selection signal under an exposed-but-read-only surface (MCP surface trimmed by the `read_only_tools` allow-list; CLI gated).
- **Claude Haiku is the headline data point.** A fast/cheap frontier model at **0.969 mcp / 0.906 cli** outclasses every open-weights *fast* model and matches the best medium/high open-weights — most starkly on CLI task-completion (0.900 vs 0.144–0.606), where weak open-weights struggle to drive `netbox-cli`.
- **The Anthropic judge needs no shim now.** mcp-common v0.32.0 (#155) makes the judge `response_format` provider-aware: for `*.anthropic.com` it omits `response_format={"type":"json_object"}` automatically (the field Anthropic's compat endpoint rejected with HTTP 400, which #137/#140 stripped via a runtime monkeypatch). All 22 evals scored with real task-completion floats; **no 400, no runtime patch**.

## Run plan, skips & failures

- **RAN:** 11 models-under-test × 2 modes (mcp, cli) = **22 evals, all `success`, 0 errors.** Models: 4 fast + 3 medium + 1 high open-weights (Together) **+** Claude Haiku 4.5 / Sonnet 4.6 / Opus 4.8.
- **SKIPPED (as model-under-test):** `openai/gpt-5.5` (`OPENAI_API_KEY` not set). The Anthropic key is present, so all three Claude models ran (unlike the rebaseline, where Anthropic was supplied only as the judge key and the Claude models stayed non-test).
- **DISABLED:** `Qwen3.7-Max` (GATED #124 — thinking-mode streaming route emits no tool-call deltas), `Kimi-K2.5-fp4` (non-serverless), `cursor/composer-2.5` (needs an agentic bridge).
- **Eval-level failures:** none. (Benign in-transcript tool errors occurred — e.g. a weak model querying `netbox_get_objects` with an invalid `status=decommissioning` enum got a NetBox HTTP 400 and was told to continue; these are read-tool mis-queries, not enforcement blocks, and do not fail the eval.)
- **Rate-limiting:** the large open-weights models (esp. Qwen3-235B, DeepSeek, MiniMax) were Together-rate-limited (hundreds of cumulative HTTP retries) under `max_connections=4`; inspect's reset-header-aware backoff recovered every one. A single judge `RateLimitError 429` (Anthropic) during a medium eval likewise retried cleanly.

## Findings → issue

- **netbox-mcp eval harness ([#146](https://github.com/vhspace/netbox-mcp/issues/146)):** `MCP_ENFORCE_READONLY` is not forwarded to the `mcp_server_stdio` child, so the #148 `ReadOnlyEnforcementMiddleware` runs in `OFF` mode during MCP-mode evals (inert backstop). Fix: have `evals/_netbox_env.py::netbox_mcp_env()` include `MCP_ENFORCE_READONLY` (when set) in the child env dict. (No score impact, but the MCP eval should genuinely exercise the server-side gate.) Same env-forwarding class as #117/#108.

## Judge configuration (reproducibility)

- Judge client (`mcp_common` `scorers._get_llm_client`) read `EVAL_JUDGE_API_KEY` (the Anthropic key, separate budget) → `EVAL_JUDGE_BASE_URL` (`https://api.anthropic.com/v1/`) → `EVAL_JUDGE_MODEL` (`claude-sonnet-4-6`, set by `run_matrix.py --judge-model`).
- **No `response_format` shim (changed vs #137/#140).** mcp-common v0.32.0 (#155) `_call_llm_judge` calls `_supports_json_object_response_format(base_url)`, which returns `False` for `*.anthropic.com`, so `response_format` is omitted for the Anthropic judge automatically. The judge prompt already mandates a bare JSON object (parsed from the response text), so dropping the field doesn't change score extraction. Verified in a 1-sample smoke (real `task_completion_score`, no HTTP 400) before the full run.
- The runner's `[WARN: judge always runs on Together regardless of prefix]` is a stale cosmetic warning; the `judge endpoint` line (`EVAL_JUDGE_BASE_URL=…anthropic.com…`) is authoritative.

## Raw logs (local, git-ignored)

- `evals/logs/matrix/enforced-mcp` (mcp) + `enforced-mcp.console.log`
- `evals/logs/matrix/enforced-cli` (cli) + `enforced-cli.console.log`

Browse with `uv run inspect view --log-dir evals/logs/matrix/enforced-mcp`.
