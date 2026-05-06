# dc-support-mcp

MCP server for datacenter vendor support portals -- ticket management for ORI Industries and IREN

## CLI: `dc-support-cli`

Run `dc-support-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/dc-support-mcp@v1.9.0 dc-support-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/dc-support-mcp@v1.9.0 dc-support-mcp
```

### Required env vars

- `ORI_PORTAL_USERNAME`: ${ORI_PORTAL_USERNAME}
- `ORI_PORTAL_PASSWORD`: ${ORI_PORTAL_PASSWORD}
- `HYPERTEC_PORTAL_USERNAME`: ${HYPERTEC_PORTAL_USERNAME}
- `HYPERTEC_PORTAL_PASSWORD`: ${HYPERTEC_PORTAL_PASSWORD}
- `IREN_PORTAL_USERNAME`: ${IREN_PORTAL_USERNAME}
- `IREN_PORTAL_PASSWORD`: ${IREN_PORTAL_PASSWORD}
- `RTB_API_KEY`: ${RTB_API_KEY}
- `O11Y_GRAFANA_USERNAME`: ${O11Y_GRAFANA_USERNAME}
- `O11Y_GRAFANA_PASSWORD`: ${O11Y_GRAFANA_PASSWORD}
- `NETBOX_TOKEN`: ${NETBOX_TOKEN}
