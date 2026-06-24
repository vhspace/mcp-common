# redfish-mcp

MCP server and CLI for Redfish BMC management -- firmware, BIOS, power, health, sensors

## CLI: `redfish-cli`

Run `redfish-cli --help` for all commands.
Install: `uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/redfish-mcp redfish-cli`

## MCP Server

```bash
uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/redfish-mcp redfish-mcp
```

### Required env vars

- `REDFISH_USER`: ${REDFISH_USER}
- `REDFISH_PASSWORD`: ${REDFISH_PASSWORD}
- `VERIFY_SSL`: ${VERIFY_SSL:-true}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
