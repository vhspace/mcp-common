# NetBox MCP Eval Suite

Evaluation scenarios for the `netbox-mcp` server, testing agents across three execution modes using [Inspect AI](https://inspect.ai-safety-institute.org.uk/).

## Prerequisites

```bash
uv sync --group dev --extra eval
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="your-token"
export TOGETHER_API_KEY="your-key"  # for LLM-as-judge scoring
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

> **Note:** Evals require a live NetBox backend behind VPN. They will not pass against a mock.

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

| # | Category | Mode | Scenario |
|---|----------|------|----------|
| 1 | Happy path | both | Device rack lookup |
| 2 | Search | both | Site-scoped device search |
| 3 | Error handling | both | Non-existent device lookup |
| 4 | Multi-step | both | Extract cluster + IP from device |
| 5 | Tool selection | mcp | BMC IP via NetBox (not Redfish) |
| 6 | Detailed query | both | Changelog with time filter |
| 7 | Ambiguity | both | Partial hostname lookup |
| 8 | Write operation | mcp | Update device description |
| 9 | Filtering | both | Cluster + status filtered list |
| 10 | IP resolution | both | All IP addresses for a device |

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
| `tool_use_scorer` | MCP, CLI | Tool selection + task completion |
| `combined_scorer` | Combined | Tool selection + task completion + interface choice |
| `parity_scorer` | (future) | Cross-mode result equivalence |
