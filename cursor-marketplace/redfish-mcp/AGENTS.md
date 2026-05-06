# redfish-mcp

MCP server and CLI for Redfish BMC management -- firmware, BIOS, power, health, sensors

## CLI: `redfish-cli`

Run `redfish-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/redfish-mcp@v2.11.1 redfish-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/redfish-mcp@v2.11.1 redfish-mcp
```

### Required env vars

- `REDFISH_USER`: ${REDFISH_USER}
- `REDFISH_PASSWORD`: ${REDFISH_PASSWORD}
- `VERIFY_SSL`: ${VERIFY_SSL:-true}
