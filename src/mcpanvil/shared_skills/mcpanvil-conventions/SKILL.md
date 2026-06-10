---
name: mcpanvil-conventions
description: Use when working on or extending an MCP server built on mcpanvil (or building a new one). Triggers on mentions of mcpanvil, FastMCP tool authoring, dual-mode, mcpanvil.cli, mcpanvil.dual_mode, or "should this be in mcpanvil?".
---

# mcpanvil Conventions

`mcpanvil` is the shared foundation for MCP servers built on it. Most
"infrastructure" work (`.env` loading, structured logging, agent-friendly
error handling, health endpoints, multi-site discovery, CLI scaffolding) is
already done — read this skill before reinventing it.

The long-form companion to this skill lives at
[`docs/AGENT_CONVENTIONS.md`](https://github.com/vhspace/mcpanvil/blob/main/docs/AGENT_CONVENTIONS.md).
Reach for it whenever the answer below points to "see the long-form doc".

## Quick decision tree

1. **Adding a new tool to an existing MCP?** → Use `@dual_mode_tool` (see
   [Recommended pattern](#recommended-pattern)). One function definition
   becomes both a FastMCP tool and a Typer CLI command.
2. **Scaffolding a new MCP from scratch?** → Start from a template repo that
   already wires every required mcpanvil feature; run
   `uv run mcp-plugin-gen audit .` to confirm.
3. **About to write your own `.env` loader / logging setup / error handler?**
   → Stop. It already exists in `mcpanvil`. Check the inventory below first.
4. **About to hand-roll a Typer CLI command for a tool you also exposed
   over MCP?** → Stop. Use the dual-mode framework.

## Inventory: what mcpanvil already provides

Use this as a checklist before writing new infrastructure code.

### Bootstrap and config
- `mcpanvil.env.load_env()` — call once at startup; existing env > repo `.env` > parent `.env`.
- `mcpanvil.config.MCPSettings` — `pydantic-settings` base. Subclass with `env_prefix`.
- `mcpanvil.version.get_version("pkg-name")` — package metadata version, falls back to `"0.0.0-dev"`.

### Credentials
- `mcpanvil.credentials.UsernamePasswordCredentialProvider` — username/password resolution with audit metadata.
- `mcpanvil.credential_chain.CachedResolver` + `EnvResolver` — token chain with TTL caching, `op://` auto-detection, `keyctl` cross-process cache.

### Logging
- `mcpanvil.logging.setup_logging(name=..., level=..., json_output=...)` — structured logs with channels (`app` / `access` / `transcript` / `trace`).
- `mcpanvil.logging.suppress_noisy_loggers()` — pins urllib3/httpx/requests/httpcore to WARNING. Called by default from `setup_logging`.
- `mcpanvil.logging.timed_operation` / `log_timing_event` — structured timing telemetry.
- `mcpanvil.logging.compute_error_fingerprint` — stable 16-char error id for dedupe.

### HTTP transport
- `mcpanvil.http.create_http_app(mcp, settings=..., access_logger=...)` — ASGI app: CORS, auth, optional access logs, request id.
- `mcpanvil.http.add_health_route(mcp, name)` — Kubernetes-style `/health` endpoint.
- `mcpanvil.auth.HttpAccessTokenAuth` — Bearer + X-API-Key middleware.

### Health
- `mcpanvil.health.health_resource(name, version, checks={...})` — standard health dict.

### Multi-site
- `mcpanvil.sites.SiteConfig` / `SiteManager` — env-var-driven multi-site discovery.
- `mcpanvil.service_discovery.NetBoxServiceDiscovery` — pull endpoints from NetBox config contexts.

### Agent error remediation
- `mcpanvil.agent_remediation.install_cli_exception_handler(app, project_repo=...)` — Typer-wide exception handler that prints the canonical "search GitHub issues → 👍 / comment / file new" block.
- `mcpanvil.agent_remediation.mcp_remediation_wrapper(project_repo=..., logger=...)` — decorator for FastMCP tools. Catches exceptions, logs a trace event, re-raises a slim two-line `ToolError` with a stable error fingerprint.
- `mcpanvil.agent_remediation.format_agent_exception_remediation(...)` — build the markdown block manually.

### Async progress
- `mcpanvil.progress.poll_with_progress(ctx, check_fn, state_key, states, timeout_s=...)` — async polling with MCP progress notifications.

### Cross-MCP hints
- `mcpanvil.hints.HintRegistry` / `ToolHint` — typed cross-MCP tool references that break at import time when tools are renamed.

### CLI scaffolding (`mcpanvil.cli`)
- `create_cli_app(name, *, project_repo, help=None)` — Typer app with `no_args_is_help=True`, `SuggestingTyperGroup`, and `install_cli_exception_handler` already attached.
- `run_cli(app, *, log_name, log_level=None)` — chains `load_env()` → `setup_logging()` → `app()`. Use as your `main()`.
- `SuggestingTyperGroup` — emits `Did you mean: 'foo', 'bar'?` for typo'd subcommands.
- `JsonOption` — reusable `--json` / `-j` Typer annotation.
- `echo_result(data, *, as_json, human_formatter=None, title=None, truncate=4096)` — single output sink. JSON mode pretty-prints with `sort_keys=True`. Pydantic-aware via `model_dump(mode="json")`.
- `PaginatedFormatter(line_fmt)` — drop-in `human_formatter` for `{count, results: [...]}` REST payloads (NetBox / AWX / MAAS).
- `poll_until(fetch, is_terminal, *, timeout_s=600, interval_s=2, on_tick=None)` — sync companion to `poll_with_progress`. Raises `PollTimeout` with `elapsed_s` / `last_value`.

### Dual-mode tools (`mcpanvil.dual_mode`)
- `@dual_mode_tool(mcp, *, cli_name=None, cli_group=None, formatters=None, cli_only=False, mcp_only=False)` — registers a function as both a FastMCP tool and a deferred CLI command.
- `build_cli_from_mcp(mcp, *, project_repo)` — materialize the Typer CLI from the registry. Built on `create_cli_app`, so all the standard wiring is attached.
- `CliContext` — shim for `fastmcp.Context` when the same function runs from the CLI.

### Plugin tooling (`mcp-plugin-gen` console script)
- `mcp-plugin-gen generate .` — emit per-platform plugin configs (Cursor / Claude Code / OpenCode / OpenHands / AGENTS.md).
- `mcp-plugin-gen audit .` — audit your repo for adoption of the required mcpanvil features (see [Audit checklist](#audit-checklist)).
- `mcp-plugin-gen doctor .` — validate `${ENV_VAR}` placeholders + 1Password CLI session.
- `mcpanvil-doctor` — credential-chain doctor (1Password / `keyctl` / env diagnostics).

### Testing (`pip install mcpanvil[testing]`)
- `mcpanvil.testing.mcp_client` — async pytest fixture for an in-process FastMCP client.
- `mcpanvil.testing.assert_tool_exists` / `assert_tool_success` — happy-path assertions.
- `mcpanvil.testing.eval` — optional LLM-as-judge eval suite (`pip install mcpanvil[eval]`).

## Recommended pattern

The canonical end-to-end shape for a new MCP server:

```python
# src/my_mcp/server.py
from fastmcp import FastMCP
from mcpanvil.dual_mode import dual_mode_tool

mcp = FastMCP("netbox-mcp")

@dual_mode_tool(mcp, cli_name="lookup-device")
def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
    """Resolve a hostname/IP to a NetBox device."""
    ...
```

```python
# src/my_mcp/cli.py
from mcpanvil.cli import run_cli
from mcpanvil.dual_mode import build_cli_from_mcp

from .server import mcp

app = build_cli_from_mcp(mcp, project_repo="your-org/netbox-mcp")

def main() -> None:
    run_cli(app, log_name="netbox_cli")
```

That is the full conventional shape. Argument names, types, and docstrings
are a single source of truth between the MCP tool and the CLI command.

## Escape hatches

- **`cli_only=True`** — for CLI-only commands (`version`, `config show`, local
  diagnostics) that don't make sense as MCP tools.
- **`mcp_only=True`** — for tools that don't make sense on the command line
  (binary streams, tools that need a live MCP session for progress).
- **Direct `@app.command()` decorators** — `build_cli_from_mcp` returns a plain
  `typer.Typer`; attach hand-written commands when the dual-mode shape doesn't
  cover the use case (interactive prompts, multi-step flows, stdin readers).
- **Skip dual-mode entirely** — call `create_cli_app(name, project_repo=...)`
  directly when you don't want FastMCP integration; you still get typo
  suggestions, the agent remediation footer, and the standard env+logging
  bootstrap.

## Output conventions

- `--json` / `-j` is always available. Dual-mode adds it automatically; with
  hand-written commands annotate the param with `JsonOption`.
- JSON output uses `sort_keys=True` for deterministic agent parsing.
- Pydantic models serialize via `model_dump(mode="json")`.
- Human output goes through `echo_result(..., human_formatter=...)`.
  Author per-type formatters as needed; the `formatters={Type: fn}` dict on
  `@dual_mode_tool` wires them up automatically.
- Use `PaginatedFormatter(line_fmt)` for `{count, results: [...]}` REST
  responses.
- Truncation is explicit (`… (N more chars)` suffix). Disable with `truncate=0`.

## Error handling conventions

- CLI apps from `create_cli_app` and `build_cli_from_mcp` already have
  `install_cli_exception_handler` attached. Unhandled exceptions print the
  agent remediation block on stderr and exit 1.
- Wrap network-talking MCP tools with `mcp_remediation_wrapper`. The wrapper
  logs a trace event with a stable error fingerprint and re-raises a slim
  two-line `ToolError` carrying that fingerprint.
- Tests using `typer.testing.CliRunner` should look at `result.exception` /
  `result.exit_code`, NOT the rendered remediation footer. `CliRunner`
  bypasses Typer's outer exception-handling path, so the footer never appears
  in `result.output`.

## Audit checklist

`uv run mcp-plugin-gen audit .` recommends every MCP use:

- `load_env`
- `setup_logging`
- `MCPSettings`
- `health_resource`
- `add_health_route`
- `mcp_remediation_wrapper`
- `install_cli_exception_handler`
- `get_version`

Run with `--strict` in CI to fail when any required feature is missing.

## Pitfalls

- **`from __future__ import annotations` is fine.** The dual-mode framework
  resolves PEP 563 string annotations via `typing.get_type_hints` against the
  function's module globals — no `ForwardRef` errors at decoration time.
- **Don't name a tool parameter `json`.** Collides with the synthetic
  `--json` flag; the framework rejects this at decoration time.
- **Don't use `set[T]` / `frozenset[T]` parameters.** Typer can't render them
  as multi-value options. Use `list[T]` instead.
- **Don't use non-`Optional` `Union[T, U]` parameters.** Typer rejects unions
  outright; only `Optional[T]` (`T | None`) is supported.
- **Pydantic input models with > 6 fields don't flatten.** They fall back to a
  single `--params '<json>'` blob. Plan tool signatures accordingly.
- **Don't `print(...)` from inside a tool body.** The CLI side captures return
  values and routes them through `echo_result`; stray prints bypass `--json`
  formatting. Log via `mcpanvil.logging`.

## Versioning

- mcpanvil follows semver. Pin downstream MCPs with a semver range
  (`mcpanvil>=0.22.0,<0.23.0`), not an exact version.
- Bump pre-commit hook revisions in `.pre-commit-config.yaml` together with
  the dependency pin.

## Examples

- Use a template repo as a canonical scaffold for a new MCP server built on
  `mcpanvil.dual_mode`.
- Migrate read-only tools onto the dual-mode framework with full MCP↔CLI
  parity tests.
- Long-form reference:
  [`docs/AGENT_CONVENTIONS.md`](https://github.com/vhspace/mcpanvil/blob/main/docs/AGENT_CONVENTIONS.md)
  in this repo.
