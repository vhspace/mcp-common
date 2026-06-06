# awx-mcp

MCP server for Ansible AWX / Automation Controller job management and orchestration

## CLI: `awx-cli`

Run `awx-cli --help` for all commands.
Install: `uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/awx-mcp awx-cli`

## MCP Server

```bash
uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/awx-mcp awx-mcp
```

### Required env vars

- `AWX_HOST`: ${AWX_HOST}
- `AWX_TOKEN`: ${AWX_TOKEN}
- `MCP_HTTP_ACCESS_TOKEN`: ${MCP_HTTP_ACCESS_TOKEN}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
