# mcp-network

Read-only MCP access to Together AI network switches (initially the ORI-TX NVIDIA Cumulus Linux fabric)

## CLI: `network-cli`

Run `network-cli --help` for all commands.
Install: `uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/mcp-network network-cli`

## MCP Server

```bash
uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/mcp-network mcp-network
```

### Required env vars

- `ORI_NETWORK_USER`: ${ORI_NETWORK_USER}
- `ORI_NETWORK_PASSWORD`: ${ORI_NETWORK_PASSWORD}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
