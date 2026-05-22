# weka-mcp

MCP server for Weka storage system management and monitoring

## CLI: `weka-cli`

Run `weka-cli --help` for all commands.
Install: `uvx --from weka-mcp weka-cli`

## MCP Server

```bash
uvx --from weka-mcp weka-mcp
```

### Required env vars

- `WEKA_HOST`: ${WEKA_HOST}
- `WEKA_PASSWORD`: ${WEKA_PASSWORD}
