# awx-mcp

MCP server for Ansible AWX / Automation Controller job management and orchestration

## CLI: `awx-cli`

Run `awx-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/awx-mcp@v1.1.0 awx-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/awx-mcp@v1.1.0 awx-mcp
```

### Required env vars

- `AWX_HOST`: ${AWX_HOST}
- `AWX_TOKEN`: ${AWX_TOKEN}
