# mcp-common — Together AI MCP monorepo

[![CI](https://github.com/togethercomputer/mcp-common/actions/workflows/ci.yml/badge.svg)](https://github.com/togethercomputer/mcp-common/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/togethercomputer/mcp-common)](https://github.com/togethercomputer/mcp-common/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Together AI's monorepo for **Model Context Protocol (MCP) servers** and the shared
**`mcp_common`** library they build on. Each server lives under `servers/<name>/`,
builds on the in-repo `mcp_common` library, and ships install configs for every
agent client (Cursor, Claude Code, OpenCode, OpenHands) from a single source of
truth — installable one-click or copy-paste, with no repo clone required.

| Path | What it is |
|------|------------|
| `servers/netbox-mcp/` | **netbox-mcp** — read-only MCP server + `netbox-cli` for querying NetBox infrastructure |
| `src/mcp_common/` | Shared library: config, logging, credentials, dual-mode tools, the plugin generator, and the eval harness |
| `cursor-marketplace/`, `claude-marketplace/`, `opencode-marketplace/`, `openhands-marketplace/` | **Generated** install artifacts — do not hand-edit |

Servers are [`uv` workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) members and source `mcp_common` in-repo via `{ workspace = true }`.

---

## Install a server

> **For agents:** pick the client you run in and apply the matching block below.
> Every install launches the server with `uvx` directly from this repo — pinned to
> a subdirectory, so there's nothing to clone. Replace the `NETBOX_*` values with
> the target NetBox. The canonical, always-current config for any server also
> lives in `*-marketplace/` (see [Staying up to date](#staying-up-to-date)).

The generic launch command (any MCP client) is:

```bash
uvx --from "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/<server>" <server>
```

### netbox-mcp

Environment: `NETBOX_URL` (NetBox base URL), `NETBOX_TOKEN` (a **read-only** API token), optional `VERIFY_SSL` (default `true`).

#### Cursor — one-click

[**▶ Add netbox-mcp to Cursor**](cursor://anysphere.cursor-deeplink/mcp/install?name=netbox-mcp&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyItLWZyb20iLCAiZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS90b2dldGhlcmNvbXB1dGVyL21jcC1jb21tb25AbWFpbiNzdWJkaXJlY3Rvcnk9c2VydmVycy9uZXRib3gtbWNwIiwgIm5ldGJveC1tY3AiXSwgImVudiI6IHsiTkVUQk9YX1VSTCI6ICIke05FVEJPWF9VUkx9IiwgIk5FVEJPWF9UT0tFTiI6ICIke05FVEJPWF9UT0tFTn0iLCAiVkVSSUZZX1NTTCI6ICIke1ZFUklGWV9TU0w6LXRydWV9In19)

Click the link (Cursor prompts to install), then fill in `NETBOX_URL` / `NETBOX_TOKEN` under Settings → MCP. Or add it manually to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

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

#### Claude Code

```bash
claude mcp add-json netbox-mcp '{"command":"uvx","args":["--from","git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp","netbox-mcp"],"env":{"NETBOX_URL":"https://netbox.example.com","NETBOX_TOKEN":"<read-only-token>"}}'
```

Or add the same `{ "mcpServers": { ... } }` block (as shown for Cursor) to your project `.mcp.json`.

#### OpenCode

Merge into `opencode.json`:

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

#### OpenHands

Add to your OpenHands MCP config (`mcp.json`):

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

---

## Staying up to date

The install commands above use **`@main`** — `uvx` runs the latest commit on the
default branch. `uvx` caches resolved environments, so to force-pull the newest
code:

```bash
uvx --refresh --from "git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/netbox-mcp" netbox-mcp
```

- **Pin a release** for reproducibility: replace `@main` with a tag, e.g. `@v2.23.0`.
- **Canonical configs:** the generated `cursor-marketplace/`, `claude-marketplace/`,
  `opencode-marketplace/`, and `openhands-marketplace/` directories always hold the
  current, ready-to-copy config for every server. **Agents:** read the file for
  your platform there rather than hand-writing config.
- These artifacts are rebuilt by the **Rebuild Marketplaces** workflow (and the
  pre-commit hook) whenever a server's source changes.

---

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

---

## Development

```bash
uv sync --all-packages --all-groups
uv run ruff check src/ tests/ servers/
uv run ruff format --check src/ tests/ servers/
uv run mypy src/ servers/netbox-mcp/src
uv run pytest -q -m "not integration and not e2e and not slow"               # mcp-common library
uv run pytest servers/netbox-mcp/tests -q -m "not integration and not e2e"   # netbox-mcp server
```

**Branching:** day-to-day work lands on **`dev`** (which runs the full CI suite);
**`main`** is protected and holds released/stable code. Promote `dev → main` via a
reviewed PR for major changes. Both branches run CI on every push and PR.

---

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

Use it in a server (workspace member) via `mcp-common = { workspace = true }` in
`[tool.uv.sources]`. The canonical, in-depth guide is
[`docs/AGENT_CONVENTIONS.md`](./docs/AGENT_CONVENTIONS.md) — read it before building
or extending a server. Module-level docstrings under `src/mcp_common/` cover the
full APIs.

## License

Apache-2.0
