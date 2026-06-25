# CLI-Discovery Eval Suite

Cross-cutting Inspect AI eval that measures whether agents can **discover and
version-check the six mcp-common `*-cli` tools** — the failure mode
[togethercomputer/mcp-common#95](https://github.com/togethercomputer/mcp-common/issues/95)
characterizes (an agent spiraling on `mcp-common` / `mcp-plugin-gen` /
`mcp-common-doctor` / `pip show mcp-common` / `uv tool list` instead of running
each `*-cli --version`).

This suite lives at the **repo root** (`evals/cli_discovery/`) rather than
under a single server because the scenarios span ALL six `*-cli` binaries
(`awx-cli`, `dc-support-cli`, `network-cli`, `netbox-cli`, `redfish-cli`,
`ufm-cli`), so they do not fit one server's per-binary `cli_tool_use_scorer`.

## Prerequisites

**None for the scenarios themselves.** `--version` and `--help` are eager,
no-credentials introspection paths on every `*-cli` — they short-circuit
before any client setup, so the eval does **not** require `AWX_TOKEN`,
`NETBOX_URL`/`NETBOX_TOKEN`, or any other service credential.

You only need:

```bash
# From the repo root. The eval extra pulls in inspect-ai + openai.
uv sync --extra eval
export TOGETHER_API_KEY="your-key"     # model-under-test + LLM-as-judge

# Optional judge decoupling (shared budget otherwise)
export EVAL_JUDGE_API_KEY="separate-judge-key"
```

The six `*-cli` binaries must be on PATH (they are in this workspace at
`/usr/local/bin/*-cli`; the `bash` sandbox inherits the parent `PATH`).

## The scorer (key new piece)

The per-binary `cli_tool_use_scorer` cannot score these scenarios:

1. It is parameterized to a **single** `cli_binary`, but the scenarios span six.
2. Its `_extract_cli_subcommands` **skips flag-only invocations**, so a bare
   `netbox-cli --version` extracts `[]` and an
   `expected_commands: ["netbox-cli --version"]` entry normalizes to `None` —
   a **vacuous pass** that would make a `--version`-discovery eval look
   perfect while measuring nothing.

This suite uses the new flag-aware, multi-binary
`cli_discovery_scorer` (`src/mcp_common/testing/eval/scorers.py`):

- Credits bash commands invoking ANY of the six `*-cli` (fallbacks like
  `pip show netbox-mcp` / `uv tool list` / `mcp-common-doctor` are NOT
  credited).
- Credits root-flag invocations: `<cli> --version` and `<cli> --help` are
  valid, matchable expected commands (captured as the flag token), not just
  subcommands. `ufm-cli version` is credited as the `version` subcommand.
- Tracks `(cli_binary, token)` pairs so `awx-cli --version` and
  `netbox-cli --version` are distinct expected items (without the binary
  pairing, six `--version` expectations would dedupe to one).
- Keeps the **LLM-as-judge `expected_behavior`** as the primary pass/fail
  signal (per the issue's recommendation); the deterministic tool-selection
  score is a secondary signal in metadata.

Unit tests: `tests/unit/test_eval_scorers.py`
(`TestExtractCliInvocations`, `TestNormalizeDiscoveryExpected`,
`TestCliDiscoveryScorer`).

## Scoring caveat

The `expected_commands` list is documentation + a secondary deterministic
signal. The **primary** pass/fail signal is the LLM-as-judge
`expected_behavior` (does the final answer report the right versions and
avoid the fallbacks?). A run that issues the right `--version` commands but
reports the wrong versions (e.g. a stale `1.1.2` for `network-cli`) still
fails on `expected_behavior`.

## Running

```bash
# Dry-run plan (no model spend)
uv run python evals/cli_discovery/run_matrix.py --dry-run

# Primary model under test — kimi (the model Hermes runs)
uv run python evals/cli_discovery/run_matrix.py --tier high --models Kimi

# 1-sample smoke against kimi via inspect directly
inspect eval evals/cli_discovery/cli_eval.py \
  --limit 1 --model together/moonshotai/Kimi-K2.7-Code

# Full kimi run
inspect eval evals/cli_discovery/cli_eval.py \
  --model together/moonshotai/Kimi-K2.7-Code
```

## Model matrix

Registry: `models.py`. Runner: `run_matrix.py` (thin wrapper over
`mcp_common.testing.eval.matrix_runner`). The primary model under test is
`together/moonshotai/Kimi-K2.7-Code` (high tier) — the latest kimi model, the
same one Hermes runs. Anthropic/OpenAI entries are gated on their env vars.

The judge is the existing default Together judge
(`together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput`).

## System prompt

The task embeds the CLI-discovery guidance from
`src/mcp_common/shared_skills/cli-discovery/SKILL.md` (falling back to the
`docs/AGENT_CONVENTIONS.md` "CLI discovery" section, then a compact inline
summary) so the eval measures whether agents can **follow that guidance**.
This lets the suite demonstrate the before/after impact of the Part-1
doc/skill fix: with the guidance embedded, kimi should reach the six `*-cli`
and report their `--version` outputs instead of the 0/5 failure in the issue.
