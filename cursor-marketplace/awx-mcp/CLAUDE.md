# awx-mcp

## What This Does
MCP server for Ansible AWX / Automation Controller. 33 tools, 3 resources, 4 prompts for managing jobs, templates, inventories, credentials, notifications, and system operations.

## Tech Stack
- Python 3.12+, FastMCP v3, mcp-common, httpx, pydantic-settings

## Development
```bash
uv sync --all-groups
uv run awx-mcp
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```

## Key Constraints
- Generic CRUD + specialized tools pattern
- Use async def for tools needing MCP Context (progress/logging)
- Supports both AWX and Red Hat Automation Controller
- See TOOL_EXAMPLES.md for response format documentation
