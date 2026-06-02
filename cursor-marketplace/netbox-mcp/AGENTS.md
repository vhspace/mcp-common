# netbox-mcp

A read-only MCP server and CLI for querying NetBox infrastructure data

## CLI: `netbox-cli`

Run `netbox-cli --help` for all commands.
Install: `uvx --from git+https://github.com/togethercomputer/mcp-common@v2.23.0#subdirectory=servers/netbox-mcp netbox-cli`

## MCP Server

```bash
uvx --from git+https://github.com/togethercomputer/mcp-common@v2.23.0#subdirectory=servers/netbox-mcp netbox-mcp
```

### Required env vars

- `NETBOX_URL`: ${NETBOX_URL}
- `NETBOX_TOKEN`: ${NETBOX_TOKEN}
- `VERIFY_SSL`: ${VERIFY_SSL:-true}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
