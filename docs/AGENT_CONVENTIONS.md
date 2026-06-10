# Agent Conventions for mcpanvil-based MCPs

> Audience: an agent (or human developer) who has just landed in an
> mcpanvil-based MCP server repo and wants to know **what `mcpanvil` already does for me**
> and **what the convention is** before writing code.
>
> This is the canonical answer to that question. Read it once before adding a
> tool, scaffolding a new MCP, or auditing an existing one.

## TL;DR

- Every mcpanvil-based MCP shares a foundation in [`mcpanvil`](https://github.com/vhspace/mcpanvil).
  Most "infrastructure" code is already provided — `.env` loading, structured
  logging, agent-friendly error handling, health endpoints, multi-site
  discovery, and CLI scaffolding.
- The recommended pattern for new tools is the **dual-mode framework**
  (`mcpanvil.dual_mode`): one function definition becomes both a FastMCP tool
  and a Typer CLI command. (See note in [Recommended pattern](#recommended-pattern-for-new-mcps) — this lands with
  [vhspace/mcpanvil#101](https://github.com/vhspace/mcpanvil/pull/101).)
- Run `uv run mcp-plugin-gen audit .` in your MCP repo to see which mcpanvil
  features you're using vs. missing.

---

## What mcpanvil provides

A curated inventory of every public surface, grouped by purpose. All names below are importable
from `mcpanvil.<module>` (most are also re-exported from `mcpanvil` directly).

### Bootstrap and configuration

| Symbol | What it does |
|---|---|
| `mcpanvil.env.load_env` | `.env` discovery + precedence (existing env > repo `.env` > parent `.env`). Idempotent; call once at startup. |
| `mcpanvil.config.MCPSettings` | `pydantic-settings` base for MCP server config. Provides `debug`, `log_level`, `log_json`, transport settings, full unified-logging knobs (`log_access`, `log_transcript`, redaction lists, …), and `github_repo` / `issue_tracker_url` for the agent remediation workflow. Subclass and add your own fields. |
| `mcpanvil.version.get_version` | Resolve the installed package version via `importlib.metadata`. Falls back to `"0.0.0-dev"` for source checkouts. |

### Credentials

| Symbol | What it does |
|---|---|
| `mcpanvil.credentials.UsernamePasswordCredentialProvider` | Resolve username/password pairs from explicit input, env vars, or 1Password `*_REF` env vars, with audit-safe metadata. Use for Redfish/IPMI/MAAS-style services. |
| `mcpanvil.credentials.CredentialCandidate` | One named credential candidate (env var keys + optional 1Password references). |
| `mcpanvil.credential_chain` | Token (single-value) chain with TTL caching. `EnvResolver` auto-detects `op://Vault/Item/field` and resolves it via the 1Password CLI. `CachedResolver` stores resolved tokens in the Linux kernel keyring (`keyctl`) so a single Touch ID prompt covers an entire agent swarm. `vault://` is reserved for OpenBao. |

### Logging and telemetry

| Symbol | What it does |
|---|---|
| `mcpanvil.logging.setup_logging` | Configures structured logs (text or JSON), names the channel, attaches a syslog handler when a platform socket exists, and calls `suppress_noisy_loggers()` by default. Accepts `trace_handler=` to route the dedicated trace channel to a durable sink (see below). |
| `mcpanvil.logging.suppress_noisy_loggers` | Pins `urllib3`, `httpx`, `requests`, and `httpcore` to `WARNING` so request-lifecycle chatter doesn't bury app logs. Skipped automatically when `level="DEBUG"`. |
| `mcpanvil.logging.timed_operation` / `log_timing_event` | Emit structured timing events on the `access` channel (`operation`, `expected_s`, `actual_s`, `ok`, `timed_out`). |
| `mcpanvil.logging.log_trace_event` / `mcp_log_trace` | Emit a diagnostic event on the **dedicated, non-stderr trace channel** (`log_channel="trace"`). The passed logger is context-only — its name is recorded as `source`; the record is emitted on `mcpanvil.trace` (never the caller's stderr). See the trace-channel note below. |
| `mcpanvil.logging.get_trace_logger` / `configure_trace_channel` / `TRACE_LOGGER_NAME` | Access and route the dedicated trace/diagnostic channel. `get_trace_logger()` returns the isolated `mcpanvil.trace` logger (`propagate=False` + default `NullHandler`); `configure_trace_channel(handler)` attaches a durable sink for the triage pipeline. |
| `mcpanvil.logging.compute_error_fingerprint` | Stable 16-char hex error id (type, message head, last frame) for dedupe. |
| `mcpanvil.logging.redact_config_from_settings` / `sanitize_transcript_value` | Redaction primitives used by the transcript channel. |

> **The trace/diagnostic channel never reaches the caller's stderr ([vhspace/mcpanvil#117](https://github.com/vhspace/mcpanvil/issues/117)).**
> Agent-remediation text, error fingerprints, and tracebacks emitted via
> `log_trace_event` (e.g. by `mcp_remediation_wrapper` and
> `install_cli_exception_handler`) are diagnostic artifacts for a **separate
> triage agent** / the failure-correlation pipeline
> ([#31](https://github.com/vhspace/mcpanvil/issues/31)) — the calling agent
> must only ever see a terse error line. To guarantee that, `log_trace_event`
> always emits on a dedicated logger (`mcpanvil.trace`) with `propagate=False`
> and a default `logging.NullHandler`, **regardless of the logger you pass**
> (the passed logger's name is preserved as the structured `source` field).
> Because it neither propagates to the root/stderr `StreamHandler` that
> `setup_logging` installs nor falls through to `logging.lastResort`, a trace
> event produces **nothing on stderr** by default.
>
> **Behavior change:** trace events therefore no longer appear in your app's
> normal log stream. To persist them for the triage pipeline, give the channel a
> durable sink that is **not** stderr — a file, a JSON/HTTP handler, etc.:
>
> ```python
> import logging
> from mcpanvil.logging import JSONFormatter, setup_logging, configure_trace_channel
>
> # Option A — at bootstrap:
> trace_sink = logging.FileHandler("/var/log/mcp/trace.jsonl")
> trace_sink.setFormatter(JSONFormatter())
> setup_logging(name="my-mcp", trace_handler=trace_sink)
>
> # Option B — anytime:
> configure_trace_channel(trace_sink)
> ```
>
> Normal app logging (`logger.info/warning/error`) is unaffected — only the
> dedicated trace/diagnostic events changed channel.

### HTTP transport

| Symbol | What it does |
|---|---|
| `mcpanvil.http.create_http_app` | ASGI app factory: CORS, optional bearer auth, optional access-log middleware, request-id propagation. Pass `settings=` to wire HTTP access logging from `MCPSettings`. |
| `mcpanvil.http.add_health_route` | Adds a `/health` route (Kubernetes liveness/readiness style). |
| `mcpanvil.http.user_agent` | Build a stable, explicit **outbound** `User-Agent` from the real `mcpanvil` version — `user_agent()` → `"mcpanvil/<ver>"`, `user_agent("X")` → `"X mcpanvil/<ver>"`. |
| `mcpanvil.auth.HttpAccessTokenAuth` | FastMCP middleware that accepts `Authorization: Bearer …` and `X-API-Key`. |

> **Convention — every MCP HTTP client MUST set an explicit `User-Agent`.**
> The default `Python-urllib/*` UA is **banned by some WAFs** (e.g. Cloudflare)
> and gets a `403` (CF Error 1010, `browser_signature_banned`). `requests`' default
> `python-requests/*` is *currently* allowed but is latent fragility — don't
> rely on it. Set the UA explicitly via `mcpanvil.http.user_agent(...)`:
>
> ```python
> from mcpanvil.http import user_agent
>
> # urllib
> req = urllib.request.Request(url, headers={"User-Agent": user_agent("my-client")})
> # requests.Session
> session.headers["User-Agent"] = user_agent("my-client")
> ```
>
> When the shared HTTP client base ([#88](https://github.com/vhspace/mcpanvil/issues/88))
> lands it will set this by default. Until a downstream MCP can depend on the
> helper's release, it may set an explicit literal (`"<name>/<version>"`) and
> switch to the helper on its next `mcpanvil` bump
> ([#121](https://github.com/vhspace/mcpanvil/issues/121)).

### Health and resources

| Symbol | What it does |
|---|---|
| `mcpanvil.health.health_resource` | Standard health-check dict (`name`, `version`, `status`, `uptime_seconds`, `checks`). Auto-degrades to `"degraded"` when any check is falsy. |

### Multi-site / discovery

| Symbol | What it does |
|---|---|
| `mcpanvil.sites.SiteConfig` / `SiteManager` | Generic multi-site manager: discover sites from `{PREFIX}_{SITE}_URL` env vars plus arbitrary fields on a `SiteConfig` subclass. Supports aliases (`{PREFIX}_SITE_ALIASES_JSON`) and a default site. |
| `mcpanvil.service_discovery.NetBoxServiceDiscovery` | Pull site service endpoints from NetBox config contexts named `site:<slug>` and expose them as typed `ServiceEndpoint` models. Secrets stay in env vars; NetBox stores `*_env` references. |

### Agent error remediation

| Symbol | What it does |
|---|---|
| `mcpanvil.agent_remediation.format_agent_exception_remediation` | Build the canonical "search GitHub issues → thumbs-up / comment / file new" markdown block. |
| `mcpanvil.agent_remediation.install_cli_exception_handler` | Register a global Typer exception handler that prints the remediation block to stderr and exits 1. |
| `mcpanvil.agent_remediation.mcp_remediation_wrapper` | Decorator for FastMCP tool functions. Catches exceptions, logs a trace event, and re-raises a slim two-line `ToolError` carrying an error fingerprint. |
| `mcpanvil.agent_remediation.mcp_tool_error_with_remediation` | Imperative form for hand-written error handlers. |

### Async progress

| Symbol | What it does |
|---|---|
| `mcpanvil.progress.poll_with_progress` | Async polling helper for FastMCP tools — sends MCP progress notifications, supports a hard timeout, accepts a `logger` for automatic timing telemetry. |
| `mcpanvil.progress.OperationStates` / `PollResult` | Typed input/output for the polling helper. |

### Cross-MCP hints

| Symbol | What it does |
|---|---|
| `mcpanvil.hints.HintRegistry` / `ToolHint` | Typed cross-MCP tool references. Each MCP exports its own `HINTS = HintRegistry(...)`; consumers import by hint id rather than hardcoding tool names, getting an import-time break when tools rename. |

### CLI scaffolding (`mcpanvil.cli`, merged in #98)

The shared building blocks every mcpanvil companion CLI uses.

| Symbol | What it does |
|---|---|
| `create_cli_app(name, *, project_repo, help=None, **typer_kwargs)` | Build a Typer app with `no_args_is_help=True`, `SuggestingTyperGroup` as the default group, and `install_cli_exception_handler` already attached. |
| `run_cli(app, *, log_name, log_level=None)` | Chain `load_env()` → `setup_logging(name=log_name)` → `app()`. Use as the `main()` body of every CLI. |
| `SuggestingTyperGroup` | Typer group that emits `Did you mean: 'foo', 'bar'?` for typo'd subcommands. Configurable via `with_options(cutoff=…, max_suggestions=…)`. Disables Typer's built-in single-suggestion path so the two don't stack. Under `--json` / `-j`, an unknown command emits a structured `{error, suggestions, available_commands}` JSON error on stderr (exit 2) instead of the prose line. |
| `JsonOption` | Reusable `--json` / `-j` Typer option annotation. Pair with `echo_result(..., as_json=json)`. |
| `echo_result(data, *, as_json, human_formatter=None, title=None, truncate=4096)` | Single output sink. JSON mode pretty-prints with `sort_keys=True` (deterministic for agent parsing), serializes Pydantic models via `model_dump(mode="json")`, and is **always emitted in full** (`truncate` is ignored so the payload always parses with `json.loads`). Human mode defers to `human_formatter` (or `str()`), supports a bolded title, and truncates with an explicit `… (N more chars)` suffix. |
| `PaginatedFormatter(line_fmt, *, show_count=True)` | Drop-in `human_formatter` for REST-style `{count, results: [...]}` payloads (NetBox, AWX, MAAS). |
| `poll_until(fetch, is_terminal, *, timeout_s=600, interval_s=2, on_tick=None)` | Sync companion to `poll_with_progress` for CLI commands waiting on AWX / MAAS / UFM terminal states. Uses `time.monotonic` so elapsed tracking is clock-skew safe. |
| `PollTimeout` | Raised by `poll_until` on timeout; carries `elapsed_s` and `last_value` attributes. |

### Dual-mode tools (`mcpanvil.dual_mode`, from [vhspace/mcpanvil#101](https://github.com/vhspace/mcpanvil/pull/101) — currently in review, not yet on `main`)

The headline capability of mcpanvil: one function definition becomes both a
FastMCP tool and a Typer CLI command. Eliminates the parallel-implementation
pattern that duplicated ~500–2000 LOC across every mcpanvil-based MCP companion CLI.

| Symbol | What it does |
|---|---|
| `@dual_mode_tool(mcp, *, name=None, cli_name=None, cli_group=None, formatters=None, cli_only=False, mcp_only=False, summary=None, **mcp_tool_kwargs)` | Decorator. Unless `cli_only=True`, calls `mcp.tool(...)` on the function; always records metadata in a per-`mcp` registry so the CLI builder can pick it up later. Returns the original function unchanged. |
| `build_cli_from_mcp(mcp, *, project_repo, name=None, help=None, before_command=None, **typer_kwargs)` | Walks the registry and materializes a Typer CLI app whose commands invoke the same Python functions the FastMCP tools do. Built on top of `create_cli_app`, so all the standard wiring is attached. Pass `before_command=<callable>` for CLI-time setup (instantiate the REST client, validate env) that runs once per real invocation and is skipped on `--help` / no-subcommand paths. |
| `CliContext` | Stand-in for `fastmcp.Context` when the same function runs from the CLI. Shims `info` / `warning` / `error` / `debug` / `log` to the stdlib logger and `report_progress` to a `[NN%] message` line on stderr. Unshimmed Context methods raise `AttributeError` rather than silently no-op'ing. |

> **Auditing Context drift:** `CliContext` deliberately shims only the handful
> of `fastmcp.Context` async methods mcpanvil-based MCPs actually call. To check
> whether a newer FastMCP exposes Context methods this shim does not cover, set
> `MCPANVIL_WARN_CONTEXT_DRIFT=1` — on the next import of
> `mcpanvil.dual_mode.cli_context` you'll get a one-time `UserWarning` listing
> the unshimmed methods. It is **off by default** (otherwise it fired on every
> import — every pytest run, CLI invocation, and conformance CI step). The
> opt-in is purely a proactive heads-up; calling an unshimmed Context method on
> a `CliContext` always raises `AttributeError` regardless of this setting
> ([#107](https://github.com/vhspace/mcpanvil/issues/107)).

> **Status:** The `mcpanvil.dual_mode` subpackage lands with
> [vhspace/mcpanvil#101](https://github.com/vhspace/mcpanvil/pull/101).
> At the time this doc was authored that PR is open and under review. Once it
> merges (and a release ships), this note can be removed and downstream MCPs
> can adopt the framework directly. Until then, the symbols described here are
> only available on the `feat/86-dual-mode-framework` branch.

### Plugin tooling (`mcp-plugin-gen` console script)

| Symbol | What it does |
|---|---|
| `mcpanvil.plugin_gen` | Reads `mcp-plugin.toml` and emits per-platform plugin configs (Cursor, Claude Code, OpenCode, OpenHands, AGENTS.md). |
| `mcpanvil.plugin_cli` | The `mcp-plugin-gen` Typer entry point — `generate`, `init`, `doctor`, `audit`, `registry-entry`, `aggregate-marketplace`. |
| `mcpanvil.marketplace_builder` | Aggregator used by the marketplace rebuild workflow. |
| `mcpanvil.doctor` | Credential-chain doctor (`mcpanvil-doctor` CLI) — validates 1Password / `keyctl` / env setup. |
| `mcpanvil.plugin_audit` | The audit dataset used by `mcp-plugin-gen audit`. See [The audit checklist](#the-audit-checklist). |
| `mcpanvil.plugin_schema.PluginConfig` | Pydantic model for `mcp-plugin.toml`. |
| `mcpanvil.plugin_precommit` | Pre-commit hook helpers. |

### Testing

| Symbol | What it does |
|---|---|
| `mcpanvil.testing.mcp_client` | Async pytest fixture for an in-process FastMCP client. |
| `mcpanvil.testing.assert_tool_exists` | Assert that an MCP tool is registered. |
| `mcpanvil.testing.assert_tool_success` | Call a tool and assert it returns successfully. |
| `mcpanvil.testing.eval` | Optional `[eval]` extra — LLM-as-judge evaluation suite (`inspect-ai`-based). |

Install with `uv add "mcpanvil[testing]"` for the assertions/fixtures and
`uv add "mcpanvil[eval]"` for the eval suite.

---

## Recommended pattern for new MCPs

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

app = build_cli_from_mcp(mcp, project_repo="your-org/example-mcp")

def main() -> None:
    run_cli(app, log_name="netbox_cli")
```

That's the full conventional shape. `build_cli_from_mcp` is built on top of
`create_cli_app`, so `no_args_is_help`, the `SuggestingTyperGroup` typo
suggester, and the agent remediation footer are wired automatically.
`run_cli` chains `load_env()` → `setup_logging()` → `app()`, so credentials
and structured logs match between the MCP server and the companion CLI by
construction.

**Why this is the default:** Before dual-mode, every mcpanvil-based MCP carried a
parallel CLI implementation of every read-only tool — same arguments,
same parsing, same error handling, just rewritten as Typer commands.
The dual-mode framework collapses that to a single function plus two import
lines, with a single source of truth for argument names, types, and docstrings.

**What it eliminates:**

- Hand-written Typer command shells for every read-only tool.
- Drift between the MCP tool signature and the CLI command signature.
- Per-MCP `--json` / `-j` output plumbing.
- Per-MCP exception-handler wiring for the agent remediation footer.
- Manual conversion of Pydantic models to flag layouts.

### Positional CLI arguments

By default every parameter becomes a `--flag`. To make the primary identifier
a natural positional (`netbox-cli lookup-device sw01` instead of
`… --hostname sw01`), annotate it with `typer.Argument(...)`:

```python
from typing import Annotated
import typer

@dual_mode_tool(mcp, cli_name="lookup-device")
def lookup_device(
    hostname: Annotated[str, typer.Argument(help="Device hostname or IP.")],
    include_interfaces: bool = False,
) -> dict:
    """Resolve a hostname/IP to a NetBox device."""
    ...
# CLI: netbox-cli lookup-device <hostname> [--include-interfaces]
```

Mix positional and option params freely (positionals first, options after).
`Annotated[T, typer.Option(...)]` keeps the flag behavior. **The MCP tool's
input schema is unaffected** — FastMCP ignores the Typer marker, so `hostname`
stays a normal required string in the tool's input schema; only the CLI
projection changes. Required vs optional follows the default: a positional with
no default is required, a Python default (`= "…"`) makes it optional.

### CLI-time setup with `before_command`

Most CLIs need one-time setup before any command runs — instantiate the REST
client, validate that required env vars / credentials are present. Pass a
`before_command` callable to `build_cli_from_mcp`:

```python
def _init() -> None:
    # raise typer.Exit / a clear error if env is missing; build the client, etc.
    ...

app = build_cli_from_mcp(mcp, project_repo="your-org/example-mcp", before_command=_init)
```

It runs once per real invocation, after Typer parses args and before the tool
function. It is **skipped on `--help` (at any level) and on a bare invocation
with no subcommand**, so `netbox-cli --help` and `netbox-cli lookup-device --help`
work without credentials. Anything it raises flows through the same
`install_cli_exception_handler` path as a tool error. This formalizes the
hand-rolled per-CLI init pattern (e.g. netbox's `_maybe_init_dual_mode_netbox_client`).

> **Until #101 lands:** Use `mcpanvil.cli` directly with hand-written
> `@app.command()` decorators for now. The migration once #101 ships is purely
> additive — drop `@dual_mode_tool` on the underlying function and replace the
> hand-written command body.

---

## When to NOT use the default

The dual-mode framework is the default; reach for an escape hatch only when
the function genuinely doesn't fit.

### `cli_only=True`

For commands that don't make sense as MCP tools — typically anything that
manipulates the local user environment.

```python
@dual_mode_tool(mcp, cli_only=True, cli_name="version")
def show_version() -> str:
    return get_version("netbox-mcp")
```

Use for: `version`, `config show`, `init`, doctor-style local diagnostics.

### `mcp_only=True`

For tools that don't make sense on the command line — typically anything
that needs a live MCP `Context` for streaming progress that isn't useful
in a terminal, or returns binary data the CLI can't render.

```python
@dual_mode_tool(mcp, mcp_only=True)
async def stream_capture(ctx: Context, host: str) -> Image:
    ...
```

### Direct `@app.command()` decorators

The Typer app returned by `build_cli_from_mcp` (or `create_cli_app`) is a plain
`typer.Typer` — feel free to attach hand-written commands when the dual-mode
shape doesn't cover the use case (interactive prompts, multi-step flows,
commands that read stdin, etc.):

```python
app = build_cli_from_mcp(mcp, project_repo="your-org/example-mcp")

@app.command("import")
def import_hosts(file: typer.FileText = typer.Argument(...)) -> None:
    """Import hosts from a CSV file (CLI-only; not exposed as a tool)."""
    ...
```

The same applies if you don't want the FastMCP integration at all — call
`create_cli_app` directly and you still get the agent remediation footer,
typo suggestions, and the standard env+logging bootstrap.

---

## Output conventions

What MCP/CLI consumers (humans, agents, pipelines) expect:

- **`--json` / `-j` flag is always available.** With dual-mode it is added
  automatically; with hand-written commands annotate the param with `JsonOption`.
- **JSON output uses `sort_keys=True`.** Deterministic key ordering means
  agents pattern-matching on shape get stable results across runs.
- **Pydantic models serialize via `model_dump(mode="json")`.** `echo_result`
  picks this up automatically; never `repr()` a model into output.
- **Human-mode output goes through `echo_result(..., human_formatter=...)`.**
  Author per-type formatters as needed; the framework's `formatters={Type: fn}`
  dict on `@dual_mode_tool` wires them up automatically.
- **Use `PaginatedFormatter` for `{count, results: [...]}` REST responses.**
  Drop-in `human_formatter` covering NetBox, AWX, and MAAS.
- **Truncation is human-mode only.** `echo_result` appends `… (N more chars)`
  rather than silently dropping the tail, and only in human mode. JSON output
  (`as_json=True`) is always complete and `json.loads`-able regardless of size —
  `truncate` is ignored there. Pass `truncate=0` to disable it in human mode too.

```python
from mcpanvil.cli import PaginatedFormatter, echo_result

device_lines = lambda d: f"{d['name']:30s}  {d['site']['slug']}  {d['status']['value']}"
echo_result(
    api_response,
    as_json=json,
    human_formatter=PaginatedFormatter(device_lines),
    title="NetBox devices",
)
```

---

## Error handling conventions

- **CLI apps get `install_cli_exception_handler` for free.** Both
  `create_cli_app` and `build_cli_from_mcp` wire it. Unhandled exceptions
  print a user-safe error line plus the canonical agent remediation block
  (search GitHub issues → 👍 / comment / file new) on stderr and exit 1.
- **MCP tools that talk to the network should be wrapped with
  `mcp_remediation_wrapper`.** It catches exceptions, logs a trace event with
  a stable `error_fingerprint`, and re-raises a slim two-line `ToolError`
  carrying that fingerprint. The full remediation context lives in the trace
  log, not in the tool error response.
- **The remediation / traceback context goes to the dedicated, non-stderr
  trace channel.** Both `mcp_remediation_wrapper` and
  `install_cli_exception_handler` route the diagnostic record through
  `log_trace_event`, which emits on `mcpanvil.trace` (`propagate=False` +
  default `NullHandler`) — so the caller's stderr only ever shows the terse
  error line, never a traceback or the remediation block. Apps that want to
  persist these diagnostics for triage must attach a durable (non-stderr) sink
  via `setup_logging(trace_handler=...)` / `configure_trace_channel(...)` (see
  the Logging section). This is the channel half of the trace-log-only design
  ([#115](https://github.com/vhspace/mcpanvil/issues/115) +
  [#117](https://github.com/vhspace/mcpanvil/issues/117)).
- **Unit tests using `typer.testing.CliRunner` should look at
  `result.exception` and `result.exit_code`, NOT the rendered remediation
  footer.** `CliRunner` bypasses Typer's outer exception-handling path, so the
  footer never appears in `result.output` during tests. (This is documented
  on `build_cli_from_mcp`'s docstring; production CLI invocations are
  unaffected.) Example:

  ```python
  result = runner.invoke(app, ["lookup-device", "missing-host"])
  assert result.exit_code != 0
  assert isinstance(result.exception, NetBoxNotFound)
  # Don't: assert "Search GitHub issues" in result.output  # ← won't appear
  ```

- **`format_agent_exception_remediation` is public.** Use it directly when
  you want the full remediation block in a custom error path.

---

## The audit checklist

`mcp-plugin-gen audit .` (backed by `mcpanvil.plugin_audit`) scans your
MCP repo's `src/` for `mcpanvil` imports and reports which features are
in use vs. missing.

Required features (audit fails when missing):

- `load_env`
- `setup_logging`
- `health_resource`
- `add_health_route`
- `mcp_remediation_wrapper`
- `get_version`

Recommended features (warning, not failure):

- `MCPSettings`
- `install_cli_exception_handler` — **satisfied by any of**
  `install_cli_exception_handler`, `create_cli_app`, or `build_cli_from_mcp`.
  The latter two wire the handler transparently, so an MCP that migrated to the
  CLI scaffolding / dual-mode framework passes the check without importing the
  handler by name ([vhspace/mcpanvil#99](https://github.com/vhspace/mcpanvil/issues/99)).

Run live in any mcpanvil-based MCP:

```bash
uv run mcp-plugin-gen audit .
# or, fail CI when required features are missing:
uv run mcp-plugin-gen audit . --strict
```

---

## Where to look for examples

- **Canonical scaffold:** [`your-org/mcp-template`](https://github.com/your-org/mcp-template).
  The starter template every new mcpanvil-based MCP forks from. Will adopt
  `mcpanvil.dual_mode` in a separate PR after #101 merges.
- **Real-world adoption:** [`your-org/example-mcp` PR #104](https://github.com/your-org/example-mcp/pull/104).
  Migrates three read-only tools onto the dual-mode framework with full
  MCP↔CLI parity tests.
- **mcpanvil itself:** This repo's `src/mcpanvil/cli/` and
  `src/mcpanvil/dual_mode/` (on `feat/86-dual-mode-framework` for now)
  are the source of truth for the API.

---

## Common pitfalls

- **`from __future__ import annotations` is fine.** Earlier `dual_mode`
  prototypes broke on PEP 563 forward refs because Typer rejects unresolved
  `ForwardRef` instances; the final implementation uses `typing.get_type_hints`
  to re-evaluate string annotations against the function's module globals.
  Tools should still feel free to use `from __future__ import annotations`.
- **Don't name a tool parameter `json`.** It collides with the synthetic
  `--json` / `-j` flag the framework appends to every CLI command. The
  decorator rejects this at decoration time with a clear error.
- **Avoid `set[T]` / `frozenset[T]` parameter types.** Typer can't render them
  as multi-value options. The framework rejects them up front with a "use
  `list[T]` instead" message.
- **Avoid non-`Optional` `Union[T, U]` parameter types.** Typer rejects unions
  outright; only `Optional[T]` (i.e. `T | None`) is supported, since that maps
  cleanly to "may be `None` at the call site". The framework rejects multi-arm
  unions at decoration time with an explicit pointer to that constraint.
- **Nested Pydantic input models get `--<field>-json` blobs.** Models with
  more than `PYDANTIC_FLATTEN_THRESHOLD` (currently 6) fields fall back to a
  single `--params '<json>'` option rather than full flattening. Plan tool
  signatures with the flattening threshold in mind.
- **Don't `print(...)` from inside a tool body.** The CLI side captures
  return values and `echo_result`s them; stray `print` calls bypass the
  `--json` plumbing. Log via `mcpanvil.logging.get_logger` (or
  `setup_logging`-named loggers) so CLI and MCP both get consistent
  formatting.

---

## Versioning convention

- mcpanvil follows semver. Minor bumps may add new APIs but never break
  existing ones; major bumps require migration notes in `CHANGELOG.md`.
- **Pin downstream MCPs with a semver range, not an exact version.** For
  example, while v0.22.x is current:

  ```toml
  # pyproject.toml
  dependencies = [
      "mcpanvil>=0.22.0,<0.23.0",
      ...
  ]
  ```

  The recent rollout audit found most MCPs were pinned to old patch versions
  (`mcpanvil==0.5.x` or similar) and missing months of audit-checked features.
  When mcpanvil ships a minor (e.g. 0.22 → 0.23), bump the pin in every
  downstream MCP in a coordinated batch.
- Pre-commit hook revisions follow the same versioning — repin the
  `.pre-commit-config.yaml` ref when bumping the dependency pin.

---

## Companion: the SKILL form

A tightened version of this doc lives at
`src/mcpanvil/shared_skills/mcpanvil-conventions/SKILL.md`. That file is
the staging ground for [vhspace/mcpanvil#95](https://github.com/vhspace/mcpanvil/issues/95)
— a future shared-skills mechanism that promotes mcpanvil-authored skills
into every downstream MCP's plugin so agents working on any MCP automatically
see this conventions doc as a Cursor / Claude skill.

Until #95 lands, this Markdown doc is the discoverable entry point. After
#95 lands, both files coexist (this doc is the long-form reference, the
SKILL.md is the agent-runtime trigger).
