# ufm-mcp

MCP server for NVIDIA UFM InfiniBand fabric management and monitoring

## CLI: `ufm-cli`

Run `ufm-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/ufm-mcp@v0.4.1 ufm-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/ufm-mcp@v0.4.1 ufm-mcp
```

### Required env vars

- `UFM_URL`: ${UFM_URL}
- `UFM_TOKEN`: ${UFM_TOKEN}
