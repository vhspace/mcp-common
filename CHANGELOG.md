# Changelog

## 0.8.0 - 2026-04-20

### Breaking changes

- `mcp_remediation_wrapper` no longer includes agent-directed remediation markdown in `ToolError` responses. Tool failures now surface as a two-line string:
  ```
  <ExcType>: <msg> (ref: <16-hex-fingerprint>)
  This failure has been logged. Continue with the primary task.
  ```
  Full failure context (stack trace, fingerprint, tool name, repo, version) is routed to the trace log via `log_trace_event`. Multi-line exception messages are flattened to a single line so the two-line contract always holds.

### Unchanged

- `install_cli_exception_handler` continues to print the full remediation block to stderr.
- `format_agent_exception_remediation` and `mcp_tool_error_with_remediation` helpers remain public and unchanged — use them directly if you want the full remediation block in custom error responses.

### Migration

No code changes required in downstream MCP servers. Bump the `mcp-common` pin to `v0.8.0`. Agent prompts that reference "follow the remediation block" should be updated; failure triage now happens via ops tooling on the trace log (see [vhspace/mcp-common#31](https://github.com/vhspace/mcp-common/issues/31) for the correlation pipeline).

## Unreleased

- Gate the `CliContext` Context-drift warning behind the
  `MCP_COMMON_WARN_CONTEXT_DRIFT` env var. `_detect_context_drift()` used to
  emit a `UserWarning` listing ~17 unshimmed `fastmcp.Context` async methods on
  every package import — noise in every pytest run, CLI invocation, and
  conformance CI step across downstream MCPs. It is now silent by default; set
  `MCP_COMMON_WARN_CONTEXT_DRIFT=1` (or `true`/`yes`/`on`) to opt in and audit
  drift (still at most once per process). The `force=True` test hook and the
  runtime `AttributeError` raised when a tool calls an unshimmed Context method
  are unchanged ([#107](https://github.com/vhspace/mcp-common/issues/107)).
- Introduce `mcp_common.dual_mode` subpackage — the headline capability of
  `mcp-common`. A single function definition becomes BOTH a FastMCP tool
  AND a Typer CLI command, eliminating the parallel-implementation pattern
  that duplicated ~500–2000+ LOC across every vhspace MCP companion CLI
  ([#86](https://github.com/vhspace/mcp-common/issues/86)).
  - Add `@dual_mode_tool(mcp, *, name=None, cli_name=None, cli_group=None,
    formatters=None, cli_only=False, mcp_only=False, summary=None,
    **mcp_tool_kwargs)` — registers a function as both a FastMCP tool and
    a deferred CLI command. MCP namespace prefix is auto-stripped from the
    CLI name (so `netbox_lookup_device` on `FastMCP("netbox")` becomes
    `lookup-device`). Returns the original function unchanged so direct
    Python callers see no indirection.
  - Add `build_cli_from_mcp(mcp, *, project_repo, name=None, help=None,
    **typer_kwargs) -> typer.Typer` — materializes a Typer CLI from the
    per-FastMCP registry populated by `@dual_mode_tool`. Built on
    `create_cli_app`, so `no_args_is_help`, `SuggestingTyperGroup`, and
    the agent remediation footer are wired automatically.
  - Add `CliContext` — minimal stand-in for `fastmcp.Context` for CLI
    runs. Shims `info`/`warning`/`error`/`debug`/`log` to the standard
    logger and `report_progress` to a `[NN%] message` line on stderr.
    Unshimmed Context methods raise `AttributeError` rather than silently
    no-op'ing, and a module-import-time warning lists Context drift
    against the installed FastMCP.
  - Parameter introspection covers `str`/`int`/`float`/`bool`/`Path`,
    `Optional[T]`, `list[T]`, `Literal[...]`, and Pydantic models
    (flattened into individual `--payload-<field>` options for ≤ 6 fields;
    fall back to `--<param>-params '<json>'` blob otherwise). Async tools
    are driven by `asyncio.run`; sync tools call through directly.
  - Re-export `dual_mode_tool`, `build_cli_from_mcp`, and `CliContext`
    from the package root.
  - Pilot adoption in downstream MCPs (netbox-mcp, gpu-diag-mcp, …) and
    `mcp-template` are tracked as separate per-MCP follow-up issues.
- Add `docs/AGENT_CONVENTIONS.md` — canonical reference for "what does
  mcp-common already provide, and what's the convention?" Curated inventory
  of every `mcp_common.*` module, the recommended dual-mode pattern, output
  and error conventions, the audit checklist, common pitfalls, and the
  versioning policy. Linked from the README as the entry point for agents
  and developers landing in any vhspace MCP
  ([#86](https://github.com/vhspace/mcp-common/issues/86),
  [#96](https://github.com/vhspace/mcp-common/issues/96)).
- Add `src/mcp_common/shared_skills/mcp-common-conventions/SKILL.md` —
  proto agent-skill paired with the conventions doc. Lives under a new
  `shared_skills/` staging directory; once
  [#95](https://github.com/vhspace/mcp-common/issues/95) lands the
  promotion mechanism will copy this bundle into every downstream MCP's
  plugin tree. The directory ships with a README explaining the staging
  contract and an empty `__init__.py` namespace marker.
- Introduce `mcp_common.cli` subpackage — shared CLI scaffolding for
  vhspace MCP companion CLIs and foundation for the dual-mode tool
  introspection framework ([#86](https://github.com/vhspace/mcp-common/issues/86)).
  - Add `SuggestingTyperGroup` — Typer group subclass that emits multi-suggestion
    `Did you mean: 'foo', 'bar'?` output for typo'd subcommands; configurable
    `cutoff` and `max_suggestions` via the `with_options()` factory. Disables
    Typer's built-in single-suggestion behavior so the two paths do not stack
    ([#93](https://github.com/vhspace/mcp-common/issues/93)).
  - Add `create_cli_app` and `run_cli` — Typer bootstrap factory that
    replaces the ~15 LOC of identical setup repeated in every vhspace MCP CLI.
    `create_cli_app` wires `no_args_is_help=True`, `SuggestingTyperGroup` as
    default `cls`, and `install_cli_exception_handler`. `run_cli` chains
    `load_env()` → `setup_logging()` → `app()`
    ([#90](https://github.com/vhspace/mcp-common/issues/90)).
  - Add `echo_result`, `JsonOption`, and `PaginatedFormatter` — output helpers
    that centralize the `--json`/`-j` flag, JSON-vs-human result rendering,
    optional bolded title, configurable truncation, and the `{count, results}`
    REST response shape ([#87](https://github.com/vhspace/mcp-common/issues/87)).
  - Add `poll_until` and `PollTimeout` — sync companion to
    `mcp_common.progress.poll_with_progress` for CLI commands that wait on
    terminal states (AWX jobs, MAAS commissioning, UFM probes). Uses
    `time.monotonic` for clock-skew-safe elapsed tracking
    ([#91](https://github.com/vhspace/mcp-common/issues/91)).
  - Re-export `create_cli_app`, `run_cli`, `SuggestingTyperGroup`, `JsonOption`,
    `echo_result`, `poll_until`, and `PollTimeout` from the package root.
- Add `mcp_common.logging.suppress_noisy_loggers()` helper that quiets
  `urllib3`, `httpx`, `requests`, and `httpcore` at `WARNING` by default; safe
  to call multiple times and accepts custom `level` / `names` overrides
  ([#92](https://github.com/vhspace/mcp-common/issues/92)).
- `setup_logging()` now calls `suppress_noisy_loggers()` by default. Skipped
  automatically when the effective level is `DEBUG`; callers can opt out
  explicitly via `setup_logging(suppress_noisy=False)`.
- Make `mcp-plugin-gen` read plugin version from `pyproject.toml` `[project].version` only
- Reject `version` in `mcp-plugin.toml` to prevent dual-source drift
- Update plugin generator starter hook pin to `mcp-common` `v0.7.0`

## 0.2.1

- Remove stale feature-branch CI triggers
- Align CHANGELOG with actual release history

## 0.2.0

- Add shared HTTP transport utilities (auth middleware, health endpoint, ASGI factory)
- Add `HttpAccessTokenAuth` FastMCP middleware (Bearer + X-API-Key)
- Add `create_http_app()` with CORS and optional auth
- Add `add_health_route()` with Kubernetes liveness/readiness probes
- Add HTTP transport settings (`transport`, `host`, `port`, `stateless_http`) to `MCPSettings`

## 0.1.0

- Initial release
- Base configuration via `MCPSettings` (pydantic-settings)
- Structured logging with JSON support
- Health check resource utility
- Version introspection helper
- Progress-aware polling utility
- Testing fixtures and assertions for pytest
