# ufm-mcp

MCP server for NVIDIA UFM InfiniBand fabric management and monitoring

## CLI: `ufm-cli`

Run `ufm-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/ufm-mcp@v1.8.3 ufm-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/ufm-mcp@v1.8.3 ufm-mcp
```

### Required env vars

- `UFM_URL`: ${UFM_URL}
- `UFM_TOKEN`: ${UFM_TOKEN}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
