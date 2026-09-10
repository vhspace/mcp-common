# mcp-common

[![CI](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/vhspace/mcp-common)](https://github.com/vhspace/mcp-common/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Shared Python library for [vhspace](https://github.com/vhspace) MCP servers:
config, logging, HTTP transport, dual-mode tools/CLIs, plugin generation, and
testing.

This repository is the **library and generator**, not an MCP server. Downstream
servers (`netbox-mcp`, `redfish-mcp`, …) depend on it.

## Start here

| If you want to… | Go to |
|---|---|
| Use mcp-common in an MCP server | [Install](#install) and [`docs/AGENT_CONVENTIONS.md`](./docs/AGENT_CONVENTIONS.md) |
| Scaffold plugin configs for Cursor / Claude / OpenCode / OpenHands | [Plugin generator](#plugin-generator) |
| Understand generated marketplace directories | [Marketplace snapshots](#marketplace-snapshots) |
| Run tests or hack on this repo | [Development](#development) |

Working on (or building) a vhspace MCP? Read
[`docs/AGENT_CONVENTIONS.md`](./docs/AGENT_CONVENTIONS.md) first. It is the
canonical inventory of every `mcp_common.*` module, conventions, and pitfalls.
A shorter skill version lives at
[`src/mcp_common/shared_skills/mcp-common-conventions/SKILL.md`](./src/mcp_common/shared_skills/mcp-common-conventions/SKILL.md).

## Install

```bash
uv add git+https://github.com/vhspace/mcp-common
```

Testing extras:

```bash
uv add "mcp-common[testing] @ git+https://github.com/vhspace/mcp-common"
```

LLM-as-judge eval extras: `mcp-common[eval]`.

## Quick start

```python
from mcp_common import load_env, MCPSettings, setup_logging
from pydantic_settings import SettingsConfigDict

load_env()  # call once at startup, before reading env / constructing settings

class MySettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="MY_SERVER_")
    api_url: str
    api_token: str

settings = MySettings()
logger = setup_logging(level=settings.log_level, json_output=settings.log_json, name="my-server")
```

Headline pattern — one function is both a FastMCP tool and a Typer CLI command:

```python
from fastmcp import FastMCP
from mcp_common.cli import run_cli
from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool

mcp = FastMCP("example-mcp")

@dual_mode_tool(mcp, cli_name="lookup-device")
def lookup_device(hostname: str) -> dict:
    """Resolve a hostname to a device."""
    ...

app = build_cli_from_mcp(mcp, project_repo="vhspace/example-mcp")

if __name__ == "__main__":
    run_cli(app, log_name="example_cli")
```

Read-only eval mode, CLI helpers, and the rest of the dual-mode contract are
documented in [`docs/AGENT_CONVENTIONS.md`](./docs/AGENT_CONVENTIONS.md).

## What you get

| Area | Module | Role |
|---|---|---|
| Env / config | `mcp_common.env`, `mcp_common.config` | `.env` loading, `MCPSettings` |
| HTTP | `mcp_common.http`, `mcp_common.auth` | ASGI factory, health, retries, `user_agent()` |
| Credentials | `mcp_common.credentials`, `mcp_common.credential_chain` | Username/password + token chain (`op://`, keyring cache) |
| Logging | `mcp_common.logging` | Structured JSON logs, redaction, trace channel |
| Dual-mode / CLI | `mcp_common.dual_mode`, `mcp_common.cli` | One function → MCP tool + Typer command |
| Sites | `mcp_common.sites` | Multi-instance discovery from env vars |
| Remediation | `mcp_common.agent_remediation` | Agent-facing error workflow |
| Plugin gen | `mcp-plugin-gen` | `mcp-plugin.toml` → Cursor / Claude / OpenCode / OpenHands |
| Testing | `mcp_common.testing` | Pytest fixtures; optional LLM-as-judge evals |

## Plugin generator

`mcp-plugin-gen` reads `mcp-plugin.toml` plus `[project].version` from
`pyproject.toml` and writes platform configs. Do not put `version` in
`mcp-plugin.toml`.

```bash
uv run mcp-plugin-gen init .          # starter mcp-plugin.toml (author: vhspace)
uv run mcp-plugin-gen generate .      # Cursor, Claude, OpenCode, OpenHands, AGENTS.md
uv run mcp-plugin-gen doctor .        # env-placeholder + optional 1Password checks
uv run mcp-plugin-gen registry-entry .
uv run mcp-plugin-gen aggregate-marketplace ./entries ./marketplace.json
```

Starter author is `vhspace`. Optional Claude marketplace metadata:

```toml
[marketplace]
categories = ["infrastructure", "operations"]
tags = ["mcp", "private", "claude"]
```

See [Private Claude Marketplace Migration](./docs/private-claude-marketplace-migration.md)
for the downstream rollout checklist.

## Marketplace snapshots

`cursor-marketplace/`, `claude-marketplace/`, `opencode-marketplace/`, and
`openhands-marketplace/` are **generated** from the latest releases of the
private vhspace MCP repos (see `.github/workflows/rebuild-marketplaces.yml`).
Do not hand-edit them as source of truth — the next rebuild overwrites them.

Plugin metadata in those snapshots is labeled `vhspace`. Downstream MCP source
still owns runtime defaults and provider integrations.

## Credential chain

Token resolution with TTL caching, 1Password `op://` refs, and Linux kernel
keyring caching. Setup (devcontainer, macOS, CI) is in
[docs/credential-chain-setup.md](./docs/credential-chain-setup.md).

```python
from mcp_common.credential_chain import CredentialChain, EnvResolver, CachedResolver, ResolvedAuth

chain = CredentialChain([
    CachedResolver(inner=EnvResolver("NETBOX_TOKEN"), key_name="mcp:netbox-token", ttl_seconds=1800),
], name="netbox")
```

`EnvResolver` uses the value as-is, or `op read` for `op://Vault/Item/field`.
`vault://` is reserved (raises `NotImplementedError`).

## Development

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest -v
```

Plugin doctor (from an MCP repo):

```bash
uv run mcp-plugin-gen doctor .
```

Devcontainer + 1Password bridging:
[DEVCONTAINER_1PASSWORD.md](./DEVCONTAINER_1PASSWORD.md).

## License

Apache-2.0
