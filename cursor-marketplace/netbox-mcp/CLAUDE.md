# netbox-mcp

## What This Does
Read-only MCP server for querying NetBox infrastructure data. Provides 5 tools, 3 static resources, 4 resource templates, and 5 workflow prompts.

## Tech Stack
- Python 3.12+, FastMCP v3, mcp-common, pydantic-settings
- uv for dependency management
- ruff + mypy strict for code quality

## Development
```bash
uv sync --all-groups
uv run netbox-mcp
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```

## Key Constraints
- Read-only: no write operations to NetBox
- Use oob_ip for BMC/Redfish, primary_ip for SSH
- Field filtering reduces token usage -- use the fields parameter

## Domain Knowledge for Agents

### Data Model
- **Sites** = physical locations (ORI-TX, 5C-OH1-H200, IREN-H200, OCI-IL)
- **Clusters** = logical groupings spanning sites (e.g., cartesia5 has 2600+ devices across ORI-TX, 5C-OH1, OCI-IL)
- **Devices** belong to a site and optionally a cluster
- A cluster name is NOT a site -- always filter by both when location matters

### Common Mistakes
- Searching for a cluster name with `netbox_search_objects` returns empty -- use `netbox_get_objects` with `filters={"cluster": "name"}` instead
- Assuming a cluster is at one site -- clusters span multiple sites, ask or check first
- Using `role` filter -- NetBox API uses `role` as a slug, not a display name; check valid values first
- Using `primary_ip` for Redfish -- always use `oob_ip` (192.168.196.x subnet at ORI)

### Network Conventions (ORI-TX)
- 192.168.229.x/24 = primary/OS network
- 192.168.196.x/24 = OOB/BMC network
- OOB IP last octet typically matches primary IP last octet
