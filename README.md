# mcp-common

[![CI](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/vhspace/mcp-common)](https://github.com/vhspace/mcp-common/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Shared utilities and testing infrastructure for Python MCP server projects.

- **Shared building blocks** — config, logging, health checks, and versioning so MCP servers don't reinvent the wheel
- **Production HTTP transport** — ASGI app factory with CORS, bearer-token auth, and Kubernetes liveness/readiness probes
- **Multi-site connection manager** — env-var-driven discovery for MCP servers spanning multiple service instances
- **Agent error remediation** — structured workflow that tells agents to search, dedupe, and file GitHub issues automatically
- **Universal plugin generator** — one `mcp-plugin.toml` produces configs for Cursor, Claude Code, OpenCode, and OpenHands
- **Cross-MCP hint registry** — typed tool references between servers that break at import time when tools are renamed

## Plugin Generator Migration (v0.7+)

`mcp-plugin-gen` now treats `pyproject.toml` `[project].version` as the only version source.

- Do not set `version` in `mcp-plugin.toml`; generation fails if present.
- Set release version in `pyproject.toml`, then run `mcp-plugin-gen generate .`.
- Repin pre-commit hooks to `mcp-common` `v0.7.0` (or newer) in each MCP repo.

## Private Claude marketplace mode

`mcp-plugin-gen` now supports private marketplace registry artifacts for Claude.

- Add optional marketplace metadata in `mcp-plugin.toml`:

```toml
[marketplace]
categories = ["infrastructure", "operations"]
tags = ["mcp", "private", "claude"]
```

- Generate a single repo entry:

```bash
uv run mcp-plugin-gen registry-entry .
```

- Generate full plugin outputs (also includes registry entry):

```bash
uv run mcp-plugin-gen generate .
```

- Aggregate many repo entries into one deterministic marketplace file:

```bash
uv run mcp-plugin-gen aggregate-marketplace /path/to/entries /path/to/marketplace.json
```

See [Private Claude Marketplace Migration](./docs/private-claude-marketplace-migration.md)
for the template and downstream MCP rollout checklist.

## Install

```bash
uv add git+https://github.com/vhspace/mcp-common
```

For testing utilities:

```bash
uv add "mcp-common[testing] @ git+https://github.com/vhspace/mcp-common"
```

## What's Included

### Configuration (`mcp_common.config`)

Base settings class built on pydantic-settings with `.env` file support:

```python
from mcp_common import MCPSettings
from pydantic_settings import SettingsConfigDict

class MySettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="MY_SERVER_")
    api_url: str
    api_token: str
    timeout: int = 30
```

Built-in fields: `debug`, `log_level`, `log_json`, optional `github_repo` (`owner/name`) and `issue_tracker_url` for agent issue workflow (see **Agent remediation** below).

### Credential provider (`mcp_common.credentials`)

Reusable username/password resolution with audit-safe metadata for MCP servers.

```python
from mcp_common.credentials import (
    CredentialCandidate,
    UsernamePasswordCredentialProvider,
)

provider = UsernamePasswordCredentialProvider(
    candidates=[
        CredentialCandidate(
            name="ORI",
            user_env="REDFISH_ORI_USER",
            password_env="REDFISH_ORI_PASSWORD",
            user_ref_env="REDFISH_ORI_USER_REF",
            password_ref_env="REDFISH_ORI_PASSWORD_REF",
        ),
    ],
    generic_candidate=CredentialCandidate(
        name="GENERIC",
        user_env="REDFISH_USER",
        password_env="REDFISH_PASSWORD",
        user_ref_env="REDFISH_USER_REF",
        password_ref_env="REDFISH_PASSWORD_REF",
    ),
    site_hint_env="REDFISH_SITE",
)

resolved = provider.resolve(host="192.168.196.97")
```

Notes:
- `*_REF` env vars resolve via `op read ...` (1Password CLI)
- plain env vars remain supported for compatibility
- audit event data never includes secret values

### Agent remediation (`mcp_common.agent_remediation`)

Standard markdown for agents when a tool or CLI raises: search issues → thumbs-up if exact duplicate, comment if new info → else open issue → continue the primary task (prefer handling via a **subagent**).

```python
from mcp_common import format_agent_exception_remediation
from mcp_common.config import MCPSettings
from pydantic_settings import SettingsConfigDict

class MySettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="MY_MCP_")
    # github_repo and issue_tracker_url inherited (optional)

text = format_agent_exception_remediation(
    exception=exc,
    project_repo=settings.github_repo or "myorg/my-mcp",
    issue_tracker_url=settings.issue_tracker_url,
    tool_or_command="my_tool",
    version="1.2.3",
    extra_lines=["MAAS site: central"],
)
# Append to MCP tool error text, log line, or stderr as appropriate.
```

**`serverUseInstructions` snippet** (paste into MCP server metadata):

```
When a tool raises an error, the handling agent should: (1) use a subagent or
focused pass so the primary task can continue; (2) search this repo's GitHub issues
for the error; (3) if a match exists — add a thumbs-up if it is an exact duplicate,
otherwise comment with new logs/repro/version; (4) if no match — open a new issue;
(5) then continue the primary task. Optional: format_agent_exception_remediation
from mcp_common for consistent markdown (github_repo / issue_tracker_url on MCPSettings).
```

**MCP tool wrapper** — catches exceptions and re-raises as `ToolError` with remediation:

```python
from mcp_common import mcp_remediation_wrapper

@mcp.tool()
@mcp_remediation_wrapper(project_repo="myorg/my-mcp")
async def my_tool(arg: str) -> str:
    ...
```

### Multi-site management (`mcp_common.sites`)

Generic manager for MCP servers that connect to multiple instances of the same service, discovered from environment variables:

```python
from mcp_common.sites import SiteConfig, SiteManager

class WekaSiteConfig(SiteConfig):
    url: str
    username: str
    password: str
    org: str | None = None

class WekaSiteManager(SiteManager[WekaSiteConfig]):
    env_prefix = "WEKA"

mgr = WekaSiteManager(WekaSiteConfig)
mgr.discover()
cfg = mgr.get_site("prod")  # or mgr.get_site() for default
```

Environment variable conventions (where `PREFIX` is `env_prefix`):

| Variable | Purpose |
|---|---|
| `{PREFIX}_{SITE}_URL` | Required — triggers auto-discovery of a site |
| `{PREFIX}_{SITE}_{FIELD}` | Any field on your `SiteConfig` subclass |
| `{PREFIX}_SITE_ALIASES_JSON` | `{"alias": "canonical_site"}` mapping |
| `{PREFIX}_DEFAULT_SITE` | Which site to return from `get_site()` with no argument |

### Logging (`mcp_common.logging`)

Structured logging with JSON mode for containers:

```python
from mcp_common import setup_logging

logger = setup_logging(level="DEBUG", json_output=True, name="my-server")
```

### Health Checks (`mcp_common.health`)

Standard health check responses:

```python
from mcp_common import health_resource

result = health_resource("my-server", "1.0.0", checks={"db": True})
result.to_dict()
# {"name": "my-server", "version": "1.0.0", "status": "healthy", ...}
```

### Version (`mcp_common.version`)

Runtime version introspection:

```python
from mcp_common import get_version

version = get_version("my-mcp-server")  # "1.2.3" or "0.0.0-dev"
```

### Progress Polling (`mcp_common.progress`)

Poll long-running operations with MCP progress notifications:

```python
from mcp_common import OperationStates, poll_with_progress

states = OperationStates(success=["complete"], failure=["error"], in_progress=["running"])
result = await poll_with_progress(ctx, check_fn, "status", states, timeout_s=300)
```

### Testing (`mcp_common.testing`)

Shared pytest fixtures and assertions for MCP servers:

```python
from mcp_common.testing import mcp_client, assert_tool_exists, assert_tool_success

@pytest.fixture
async def client():
    async for c in mcp_client(app):
        yield c

@pytest.mark.anyio
async def test_tools(client):
    await assert_tool_exists(client, "my_tool")
    result = await assert_tool_success(client, "my_tool", {"arg": "value"})
```

## Development

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest -v
```

## Bootstrap / doctor

Use plugin doctor checks before first run:

```bash
uv run mcp-plugin-gen doctor .
```

This validates:
- referenced `${ENV_VAR}` placeholders in `mcp-plugin.toml` server env
- optional 1Password CLI/session readiness (`op --version`, `op whoami`)

For devcontainers:
- prefer forwarding host env into container runtime (`remoteEnv` / `${localEnv:...}`)
- keep desktop agent socket integration as optional, OS-specific best effort

See [Devcontainer + 1Password Secret Bridging](./DEVCONTAINER_1PASSWORD.md)
for host/container setup details.

## License

Apache-2.0
