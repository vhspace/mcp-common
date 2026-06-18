# dc-support-mcp

MCP server for datacenter vendor support portals -- ticket management for ORI Industries and IREN

## CLI: `dc-support-cli`

Run `dc-support-cli --help` for all commands.
Install: `uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/dc-support-mcp dc-support-cli`

## MCP Server

```bash
uvx --from git+https://github.com/togethercomputer/mcp-common@main#subdirectory=servers/dc-support-mcp dc-support-mcp
```

### Required env vars

- `ORI_PORTAL_USERNAME`: ${ORI_PORTAL_USERNAME}
- `ORI_PORTAL_PASSWORD`: ${ORI_PORTAL_PASSWORD}
- `HYPERTEC_PORTAL_USERNAME`: ${HYPERTEC_PORTAL_USERNAME}
- `HYPERTEC_PORTAL_PASSWORD`: ${HYPERTEC_PORTAL_PASSWORD}
- `IREN_FRESHDESK_API_KEY`: ${IREN_FRESHDESK_API_KEY}
- `IREN_FRESHDESK_URL`: ${IREN_FRESHDESK_URL:-https://iren.freshdesk.com}
- `IREN_PORTAL_USERNAME`: ${IREN_PORTAL_USERNAME}
- `IREN_PORTAL_PASSWORD`: ${IREN_PORTAL_PASSWORD}
- `RTB_API_KEY`: ${RTB_API_KEY}
- `RTB_LINEAR_TEAM_KEY`: ${RTB_LINEAR_TEAM_KEY:-}
- `LINEAR_API_KEY`: ${LINEAR_API_KEY}
- `O11Y_GRAFANA_USERNAME`: ${O11Y_GRAFANA_USERNAME}
- `O11Y_GRAFANA_PASSWORD`: ${O11Y_GRAFANA_PASSWORD}
- `NETBOX_TOKEN`: ${NETBOX_TOKEN}
- `ORI_BASE_URL`: ${ORI_BASE_URL:-https://oriindustries.atlassian.net}
- `HYPERTEC_BASE_URL`: ${HYPERTEC_BASE_URL:-https://hypertec-cloud.atlassian.net}
- `IREN_BASE_URL`: ${IREN_BASE_URL:-https://support.iren.com}
- `RTB_BASE_URL`: ${RTB_BASE_URL:-https://rtb.together.ai}
- `NETBOX_URL`: ${NETBOX_URL:-https://i.together.ai}
- `GRAFANA_AM_PROXY_BASE`: ${GRAFANA_AM_PROXY_BASE:-https://monitoring-admin.internal.together.ai/grafana/api/alertmanager}

## Generated Files — Do Not Edit

`.cursor-plugin/`, `.claude-plugin/`, `.opencode/`, `.openhands/`,
`AGENTS.md`, `opencode.json`, `.mcp.json`, and `hooks/` are generated
by `mcp-plugin-gen` from `mcp-plugin.toml`.
Edit canonical sources (`mcp-plugin.toml`, `skills/*/SKILL.md`,
`rules/*.mdc`) and commit — the pre-commit hook regenerates all copies.
