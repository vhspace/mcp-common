# UFM MCP Server

MCP server for NVIDIA Unified Fabric Manager (UFM). Provides read-only operational tools for InfiniBand fabric monitoring, triage, and log analysis.

## Tech Stack

- Python 3.12+, uv for package management
- FastMCP 2.13+ for MCP protocol
- httpx for HTTP client
- Pydantic + pydantic-settings for config
- pytest for testing, ruff for linting

## Project Structure

```
src/ufm_mcp/
  server.py         # FastMCP server, all tool definitions, entry point
  config.py         # Pydantic Settings (env vars, CLI args)
  site_manager.py   # Multi-site UFM connection management
  helpers.py        # Shared utilities (parsing, serialization)
  ufm_client.py     # Thin httpx wrapper for UFM REST API
tests/              # pytest test suite
```

## Development

```bash
uv sync --group dev       # Install deps
uv run python -m pytest   # Run tests
uv run ruff check .       # Lint
uv run ruff format .      # Format
```

## Architecture

- SiteManager handles multi-site UFM connections without global mutable state
- Tools use site= parameter to target specific sites without side-effects
- helpers.py contains all shared parsing/serialization to avoid duplication
- Write operations (system dumps, log history) require explicit allow_write=true

## Conventions

- Tool names: ufm_ prefix, snake_case
- All tools return dict with "ok" key
- Use ToolError for user-facing errors
- Never hardcode credentials; all via env vars
- Use uv, never pip
