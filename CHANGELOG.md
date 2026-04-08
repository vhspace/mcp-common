# Changelog

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
