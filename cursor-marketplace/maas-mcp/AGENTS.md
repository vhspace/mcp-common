# maas-mcp

MCP server and CLI for Canonical MAAS bare-metal provisioning and lifecycle management

## CLI: `maas-cli`

Run `maas-cli --help` for all commands.
Install: `uvx --from maas-mcp maas-cli`

## MCP Server

```bash
uvx --from maas-mcp maas-mcp
```

### Required env vars

- `MAAS_URL`: ${MAAS_URL}
- `MAAS_API_KEY`: ${MAAS_API_KEY}
