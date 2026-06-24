# mcp-common — Together AI MCP monorepo

[![CI](https://github.com/togethercomputer/mcp-common/actions/workflows/ci.yml/badge.svg)](https://github.com/togethercomputer/mcp-common/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/togethercomputer/mcp-common)](https://github.com/togethercomputer/mcp-common/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Together AI's monorepo for **Model Context Protocol (MCP) servers** and the shared
**`mcp_common`** library they build on. Each server lives under `servers/<name>/`
as an independent `uv` project that pins the `mcp_common` library by release tag,
and ships ready-to-use install configs for every agent client (Cursor, Claude
Code, OpenCode, OpenHands) — installable one-click or copy-paste, no clone needed.

> **This repo is private.** Installs fetch it over git, so authenticate to GitHub
> first (`gh auth login`, or a PAT with `repo` read access).

## Install an MCP server

### netbox-mcp

Read-only NetBox MCP server + `netbox-cli`. Env: `NETBOX_URL` (NetBox base URL),
`NETBOX_TOKEN` (a **read-only** API token), optional `VERIFY_SSL` (default `true`).

**Cursor — one-click:**

[**▶ Add netbox-mcp to Cursor**](cursor://anysphere.cursor-deeplink/mcp/install?name=netbox-mcp&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyItLWZyb20iLCAiZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS90b2dldGhlcmNvbXB1dGVyL21jcC1jb21tb25AbWFpbiNzdWJkaXJlY3Rvcnk9c2VydmVycy9uZXRib3gtbWNwIiwgIm5ldGJveC1tY3AiXSwgImVudiI6IHsiTkVUQk9YX1VSTCI6ICIke05FVEJPWF9VUkx9IiwgIk5FVEJPWF9UT0tFTiI6ICIke05FVEJPWF9UT0tFTn0iLCAiVkVSSUZZX1NTTCI6ICIke1ZFUklGWV9TU0w6LXRydWV9In19)

Cursor prompts to install; then set `NETBOX_URL` / `NETBOX_TOKEN` under Settings → MCP. Or add it manually to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

```json
{
  "mcpServers": {
    "netbox-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp", "netbox-mcp"],
      "env": { "NETBOX_URL": "https://netbox.example.com", "NETBOX_TOKEN": "<read-only-token>", "VERIFY_SSL": "true" }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add-json netbox-mcp '{"command":"uvx","args":["--from","git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp","netbox-mcp"],"env":{"NETBOX_URL":"https://netbox.example.com","NETBOX_TOKEN":"<read-only-token>"}}'
```

Or add the same `{ "mcpServers": { ... } }` block (shown above) to your project `.mcp.json`.

**OpenCode** — merge into `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "netbox-mcp": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp", "netbox-mcp"],
      "environment": { "NETBOX_URL": "https://netbox.example.com", "NETBOX_TOKEN": "<read-only-token>", "VERIFY_SSL": "true" },
      "enabled": true
    }
  }
}
```

**OpenHands** — add to your MCP config (`mcp.json`):

```json
{
  "mcpServers": {
    "netbox-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp", "netbox-mcp"],
      "env": { "NETBOX_URL": "https://netbox.example.com", "NETBOX_TOKEN": "<read-only-token>" }
    }
  }
}
```

> **Any client:** the generic launch command is
> `uvx --from "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/<server>" <server>`.
> The always-current per-client config for every server also lives under
> `cursor-marketplace/`, `claude-marketplace/`, `opencode-marketplace/`, and
> `openhands-marketplace/`. See [Staying up to date](#staying-up-to-date).

## MCP servers

| Server | What it does | Source |
|--------|--------------|--------|
| **[netbox-mcp](./servers/netbox-mcp/)** | Read-only NetBox infrastructure queries — MCP server + `netbox-cli` | [`servers/netbox-mcp/`](./servers/netbox-mcp/) · [README](./servers/netbox-mcp/README.md) |
| **[mcp-network](./servers/mcp-network/)** | Read-only network-switch queries (ORI-TX Cumulus fabric) — MCP server + `network-cli` | [`servers/mcp-network/`](./servers/mcp-network/) · [README](./servers/mcp-network/README.md) |

More servers are added under `servers/` over time, each installable the same way.

## Repo layout

| Path | What it is |
|------|------------|
| `servers/<name>/` | Each MCP server — an **independent `uv` project** (own `uv.lock`) that pins `mcp_common` by git tag |
| `src/mcp_common/` | Shared library: config, logging, credentials, dual-mode tools, the plugin generator, and the eval harness |
| `cursor-marketplace/`, `claude-marketplace/`, `opencode-marketplace/`, `openhands-marketplace/` | **Generated** install artifacts — do not hand-edit |

`mcp_common` and the servers version and release **independently** (per-package tags
`mcp-common-v*` / `netbox-mcp-v*`); see [`docs/RELEASING.md`](./docs/RELEASING.md).

## Staying up to date

The install commands above use **`@main`** — `uvx` runs the latest commit on the
default branch. `uvx` caches resolved environments, so to force-pull the newest
code:

```bash
uvx --refresh --from "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp" netbox-mcp
```

- **Pin a release** for reproducibility: replace `@main` with a server tag, e.g. `@netbox-mcp-v2.23.0`.
- **Canonical configs:** the generated `*-marketplace/` directories always hold the
  current, ready-to-copy config for every server. **Agents:** read the file for
  your platform there rather than hand-writing config.
- These artifacts are rebuilt by the **Rebuild Marketplaces** workflow (and the
  pre-commit hook) whenever a server's source changes.

## Add or change a server

Each server has one **`mcp-plugin.toml`** (the single source of truth for its name,
launch command, env, skills, rules, and hooks). For monorepo-hosted servers it sets
`subdirectory = "servers/<name>"` so generated installs resolve the right package.
After editing it, regenerate the per-server artifacts and the marketplaces:

```bash
uv run mcp-plugin-gen generate servers/<name>
uv run python -m mcp_common.marketplace_builder --repos-dir servers --output-dir .
```

(The pre-commit hook regenerates these automatically on commit.)

## Development

The library and each server are **separate `uv` projects** — work in each directory:

```bash
# mcp-common library (repo root)
uv sync --all-groups
uv run ruff check src/ tests/ && uv run mypy src && uv run pytest -q -m "not integration and not e2e and not slow"

# netbox-mcp server
cd servers/netbox-mcp
uv sync --all-groups
uv run ruff check src/ tests/ && uv run pytest -q -m "not integration and not e2e"
```

**Branching:** day-to-day work lands on **`dev`** (full CI per project); **`main`**
is protected (PR + 1 review) and holds released/stable code. Promote `dev → main`
via a reviewed PR for major changes.

## The `mcp_common` library

`mcp_common` is the shared foundation every server builds on, so servers don't
reinvent config loading, logging, credentials, CLI scaffolding, or eval tooling.
Highlights:

- **Dual-mode tools** (`mcp_common.dual_mode`) — one function becomes both a FastMCP tool and a Typer CLI command, plus a server-side `MCP_ENFORCE_READONLY` backstop for read-only evals.
- **Universal plugin generator** (`mcp_common.plugin_gen` / `mcp-plugin-gen`) — one `mcp-plugin.toml` produces Cursor, Claude Code, OpenCode, and OpenHands configs.
- **Config, logging, health, version, progress** — `MCPSettings`, structured/JSON logging with channels, health resources, version introspection, and MCP progress polling.
- **Credentials** (`mcp_common.credentials`, `mcp_common.credential_chain`) — username/password and token resolution with 1Password `op://` auto-detection and kernel-keyring caching.
- **HTTP transport** (`mcp_common.create_http_app`) — ASGI app with CORS, bearer-token auth, and Kubernetes liveness/readiness probes.
- **Agent remediation + eval harness** (`mcp_common.agent_remediation`, `mcp_common.testing`) — structured error→issue workflow and the shared LLM-as-judge eval infrastructure.

Servers consume it by **pinning a release tag** in `[tool.uv.sources]`, e.g.
`mcp-common = { git = "https://github.com/togethercomputer/mcp-common", tag = "mcp-common-v0.37.0" }`,
so each server adopts a new `mcp_common` deliberately (see [`docs/RELEASING.md`](./docs/RELEASING.md)).
The canonical, in-depth guide is [`docs/AGENT_CONVENTIONS.md`](./docs/AGENT_CONVENTIONS.md) — read it before building or extending a server.

## License

Apache-2.0
