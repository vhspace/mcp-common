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
