# netbox-mcp eval matrix — preserved results

**Run date (UTC):** 2026-05-29  
**netbox-mcp:** v2.15.0  |  **mcp-common:** v0.25.0  
**Judge model (LLM-as-judge):** `Qwen/Qwen3-235B-A22B-Instruct-2507-tput`  
**Max connections:** 1 (serial — judge rate-limits under concurrency)  
**Scenarios:** mcp mode = 16, cli mode = 16 (17 total; mode-filtered per scenario)  
**NetBox access:** READ-ONLY (no mutations).  
**Cost / runtime:** ~2.3M model-under-test tokens (Together credits) across 10 successful evals (+4 fast-failing) plus LLM-judge calls; ~1h59m total wall-clock, serial.  

## Model comparison (mcp vs cli)

| Model | Tier | mcp acc | mcp C/P/I | cli acc | cli C/P/I | mcp task-compl | cli task-compl |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | fast | 0.531 | 3/11/2 | 0.031 | 0/1/15 | 0.219 | 0.000 |
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | fast | 0.750 | 10/4/2 | 0.438 | 1/12/3 | 0.688 | 0.469 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` | medium | 0.938 | 14/2/0 | 0.469 | 0/15/1 | 0.938 | 0.844 |
| `deepseek-ai/DeepSeek-V4-Pro` | medium | 0.938 | 14/2/0 | 0.312 | 0/10/6 | 0.925 | 0.531 |
| `MiniMaxAI/MiniMax-M2.7` | medium | 0.844 | 11/5/0 | 0.344 | 0/11/5 | 0.844 | 0.594 |
| `Qwen/Qwen3.7-Max` | high | **error** | 0/0/0 | **error** | 0/0/0 | - | - |
| `moonshotai/Kimi-K2.5-fp4` | high | **error** | 0/0/0 | **error** | 0/0/0 | - | - |

> **C/P/I** = CORRECT / PARTIAL / INCORRECT sample counts. **acc** = inspect `accuracy` metric = (CORRECT + 0.5·PARTIAL) / N (matches the runner's table). **task-compl** = mean LLM-judge task-completion score (0–1).

> **Reading the CLI column:** the deterministic *tool-selection* dimension scores ~0 in CLI mode because the agent invokes `bash`, not the MCP tool names the scenarios list as `expected_tools` (see `evals/cli_eval.py`). A sample needs tool-sel ≥ 0.8 **and** task-completion ≥ 0.7 to be CORRECT, so CLI runs cap at PARTIAL for those scenarios. **The `cli task-compl` column is the fair cross-mode signal** — it reflects whether the CLI agent actually answered the question.

## Takeaways

- **MCP mode, capability scales with model size:** `Qwen3-235B` and `DeepSeek-V4-Pro` lead at **0.938** acc (14/16 CORRECT), `MiniMax-M2.7` 0.844, `Llama-3.3-70B` 0.750, and the small `Qwen3.5-9B` trails at 0.531.
- **CLI `acc` looks low by construction, not by failure:** the deterministic tool-selection dimension is ~0 in CLI mode (agent calls `bash`), capping most samples at PARTIAL. The fair signal — **mean task-completion** — stays high for strong models (`Qwen3-235B` 0.844, `MiniMax-M2.7` 0.594, `DeepSeek-V4-Pro` 0.531), i.e. they *do* answer correctly via netbox-cli.
- **Two high-tier models are unavailable on Together serverless** (see failures below): `Qwen3.7-Max` needs streaming; `Kimi-K2.5-fp4` needs a dedicated endpoint. Both passed the catalog-listing check but fail at invocation — a registry/provider gap to address separately.
- **Read-only & serial held:** all NetBox access was read-only (no mutations); judge rate-limits were absorbed by inspect's retry/backoff with `--max-connections 1`.

## Run plan, skips & failures

- **RAN:** 7 open-weights models × 2 modes (mcp, cli).
- **SKIPPED:** `openai/gpt-5.5` (OPENAI_API_KEY not set), `anthropic/claude-opus-4-8` (ANTHROPIC_API_KEY not set).
- **DISABLED:** `cursor/composer-2.5` (Cursor SDK is agentic, not an inspect chat provider; needs a custom bridge (deferred)).
- **Eval-level failures:** 2 model(s) errored identically in **both** mcp and cli modes (the runner recorded the error and continued the sweep — resilience worked):
  - `Qwen/Qwen3.7-Max` [mcp, cli]: HTTP 400 `streaming_required` — Together returns `"This model only supports streaming. Set stream:true"`. Inspect’s Together provider issues non-streaming chat-completions, which this model rejects. Not a NetBox/eval-logic issue; needs a streaming-enabled provider config.
  - `moonshotai/Kimi-K2.5-fp4` [mcp, cli]: HTTP 400 `model_not_available` — Together returns `"Unable to access non-serverless model moonshotai/Kimi-K2.5-fp4 ... create and start a new dedicated endpoint"`. The slug is in the catalog listing but is not serverless-invokable; it requires a paid dedicated endpoint.

## Per-scenario breakdown — notable scenarios

Aggregated across all RAN models (CORRECT/PARTIAL/INCORRECT counts, and mean task-completion).

| Scenario (tags) | Mode | C/P/I (across models) | mean task-compl |
|---|---|---|---|
| What IP addresses are assigned to research-common-h100-001… (happy_path, ip_resolution) | mcp | 4/1/0 | 0.800 |
| What IP addresses are assigned to research-common-h100-001… (happy_path, ip_resolution) | cli | 0/3/2 | 0.500 |
| Find the NetBox device whose provider machine ID is gpu061… (provider_machine_id, lookup) | mcp | 3/1/1 | 0.600 |
| Find the NetBox device whose provider machine ID is gpu061… (provider_machine_id, lookup) | cli | 0/4/1 | 0.700 |
| Which NetBox device has primary IP 10.49.5.61? (ip_resolution, lookup) | mcp | 2/2/1 | 0.600 |
| Which NetBox device has primary IP 10.49.5.61? (ip_resolution, lookup) | cli | 0/1/4 | 0.200 |
| Give me the out-of-band (BMC) summary for device a6177514-… (oob, bmc, pydantic_return) | mcp | 4/1/0 | 0.900 |
| Give me the out-of-band (BMC) summary for device a6177514-… (oob, bmc, pydantic_return) | cli | 0/3/2 | 0.400 |
| List all devices in the reflection cluster and tell me how… (large_result, cluster, pagination) | mcp | 2/3/0 | 0.500 |
| List all devices in the reflection cluster and tell me how… (large_result, cluster, pagination) | cli | 0/4/1 | 0.700 |

## Raw logs (local, git-ignored)

Full inspect `.eval` logs (per-sample transcripts, tool calls, judge rationale) are
**not committed** (large; `evals/logs/` is git-ignored). They live locally at:

- `evals/logs/matrix/2026-05-29-mcp` (mcp)
- `evals/logs/matrix/2026-05-29-cli` (cli)

Browse them with the inspect viewer:

```bash
uv run inspect view --log-dir evals/logs/matrix/2026-05-29-mcp
uv run inspect view --log-dir evals/logs/matrix/2026-05-29-cli
```

