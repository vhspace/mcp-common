# redfish-mcp

## What This Does
MCP server for Redfish BMC management. 14 tools for read/write operations on server hardware: BIOS, firmware, power, user accounts.

## Tech Stack
- Python 3.12+, FastMCP v3, mcp-common, requests
- Hardware database (JSON) for vendor-specific documentation
- Agent observation system (SQLite)
- MCP features: tools, resources, prompts, completions, logging, progress, tasks, elicitation

## Development
```bash
uv sync --all-groups
uv run redfish-mcp
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest -m "not integration"
```

## Key Constraints
- Always use oob_ip (BMC) not primary_ip (OS) for Redfish
- Write tools (set_bios, update_firmware) require explicit confirmation
- Hardware DB in hardware_db/ has vendor-specific docs
- See AI_AGENT_GUIDE.md for agent integration patterns
