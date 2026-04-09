# Devcontainer + 1Password Secret Bridging

This guide covers the supported pattern for MCP servers that run inside a
devcontainer while secrets remain managed in 1Password.

## Goals

- Keep long-lived secrets out of repository files.
- Avoid requiring desktop socket integration for all platforms.
- Ensure MCP tools can resolve credentials without exposing secret values to agents.

## Recommended pattern

1. Resolve secrets on the **host** (`op read`, `op run`, or Connect/API).
2. Forward only required runtime env vars into the container via
   `remoteEnv` + `${localEnv:...}`.
3. Start MCP servers inside the container using those forwarded vars.

## Platform notes

- **macOS:** Desktop socket bridging can be used, but treat it as optional.
  Keep a non-socket fallback via forwarded env vars.
- **Linux:** Prefer env forwarding or Connect/API path.
  Do not depend on a macOS-specific socket path.

## Minimal checks

Use the helper script from host or inside container:

```bash
uv run python scripts/check_1password_bridge.py REDFISH_USER_REF REDFISH_PASSWORD_REF
```

If your MCP config references direct env vars (for example `${REDFISH_USER}`),
check those names instead.

## Security rules

- Never commit secret values in `mcp-plugin.toml`, `mcp.json`, or `.env`.
- Only log credential source metadata, never secret values.
- Agents must receive tool results only; MCP servers handle secret retrieval.
