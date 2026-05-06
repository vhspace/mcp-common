# weka-mcp

## What This Does
MCP server for Weka storage system. Provides tools for cluster monitoring, filesystem management, container health, and S3 operations.

## Tech Stack
- Python 3.12+, FastMCP v3, httpx, pydantic-settings
- uv for dependency management

## Development
```bash
uv sync --all-groups
uv run weka-mcp
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```

## Key Constraints
- Supports Weka 4.4.x API
- Multi-cluster support via environment configuration
