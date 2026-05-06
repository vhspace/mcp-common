# dc-support-mcp

## What This Does
MCP server for datacenter vendor support portals. Manages tickets across ORI Industries and IREN portals using Playwright-based web scraping.

## Tech Stack
- Python 3.12+, FastMCP v3, Playwright, BeautifulSoup
- Cookie caching for performance (13x speedup)
- Multi-vendor architecture with vendor registry pattern

## Development
```bash
uv sync --all-groups
playwright install chromium
uv run dc-support-mcp
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest -m "not e2e"
```

## Key Constraints
- E2E tests require vendor portal credentials
- Playwright must be installed separately (`playwright install chromium`)
- Cookie files contain sensitive data -- never commit
