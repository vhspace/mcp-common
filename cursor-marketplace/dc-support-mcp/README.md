# DC Support MCP

[![CI](https://github.com/vhspace/dc-support-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/dc-support-mcp/actions/workflows/ci.yml)
[![Release](.github/badges/release.svg)](https://github.com/vhspace/dc-support-mcp/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

Model Context Protocol (MCP) server for querying datacenter vendor support portals.

## Features

- **Multi-vendor architecture** with extensible handler system
- **Ori Industries** (Atlassian Service Desk) support portal integration
- **IREN** (Freshdesk) support portal integration via web scraping
- Fast API calls with cookie caching (13x speedup for ORI)
- Automatic authentication via Playwright browser automation
- httpOnly cookie extraction and session auto-refresh
- Knowledge base search for IREN articles

## MCP Tools

| Tool | Description | Vendors |
|------|-------------|---------|
| `get_vendor_ticket` | Fetch a ticket with full details (summary, status, comments) | ORI, IREN |
| `list_vendor_tickets` | List tickets filtered by status (open/closed/all) | ORI, IREN |
| `add_vendor_comment` | Post a comment on a ticket (public or internal) | ORI |
| `create_vendor_ticket` | Create an Infrastructure Support ticket via form automation | ORI |
| `search_vendor_kb` | Search knowledge base articles by keyword (cached) | IREN |
| `get_vendor_kb_article` | Get a KB article with full content | IREN |

## Quick Start

### Local Development

```bash
# Install dependencies
uv sync --all-groups

# Install Playwright browsers
uv run playwright install chromium

# Set credentials (add for each vendor you want to use)
export ORI_PORTAL_USERNAME="your@email.com"
export ORI_PORTAL_PASSWORD="yourpassword"
export IREN_PORTAL_USERNAME="your@email.com"
export IREN_PORTAL_PASSWORD="yourpassword"

# Run setup check
uv run dc-support-setup --check
```

### Docker

```bash
docker build -f Dockerfile -t dc-support-mcp .

docker run --rm -i \
  -e ORI_PORTAL_USERNAME=your@email.com \
  -e ORI_PORTAL_PASSWORD=yourpassword \
  dc-support-mcp
```

### MCP Configuration

Add to your MCP client config (`.mcp.json`, Claude Desktop, Cursor, etc.):

```json
{
  "mcpServers": {
    "dc-support-mcp": {
      "command": "uv",
      "args": ["run", "dc-support-mcp"],
      "env": {
        "ORI_PORTAL_USERNAME": "your@email.com",
        "ORI_PORTAL_PASSWORD": "yourpassword"
      }
    }
  }
}
```

## Usage

### Python API

```python
from dc_support_mcp.vendors import VendorRegistry, OriVendorHandler, IrenVendorHandler

registry = VendorRegistry(verbose=True)
registry.register("ori", OriVendorHandler)
registry.register("iren", IrenVendorHandler)

# Get ORI ticket
ori = registry.get_handler("ori")
ticket = ori.get_ticket("SUPP-1556")
print(f"{ticket['summary']}: {ticket['status']}")

# List tickets
tickets = ori.list_tickets(status="open", limit=5)
for t in tickets:
    print(f"  {t['id']}: {t['summary']}")
```

### MCP Tool Examples

```bash
# List available tools
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | uv run dc-support-mcp

# Fetch a ticket
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_vendor_ticket","arguments":{"vendor":"ori","ticket_id":"SUPP-1556"}}}' | uv run dc-support-mcp
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              DC Support MCP Server                      │
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │          FastMCP Protocol Layer                    │ │
│  │  6 tools: get/list/create/comment/search/kb       │ │
│  └───────────────────────────────────────────────────┘ │
│                         ↓                               │
│  ┌───────────────────────────────────────────────────┐ │
│  │           Vendor Registry                         │ │
│  │  • Lazy init with env-var credentials             │ │
│  │  • Handler caching & lifecycle                    │ │
│  └───────────────────────────────────────────────────┘ │
│                         ↓                               │
│  ┌─────────────────┬──────────────────┬──────────────┐ │
│  │  ORI Handler    │  IREN Handler    │  (Extensible)│ │
│  │  (Atlassian)    │  (Freshdesk)     │              │ │
│  ├─────────────────┼──────────────────┤              │ │
│  │ • Cookie cache  │ • Cookie cache   │              │ │
│  │ • REST API      │ • Playwright     │              │ │
│  │ • Auto-refresh  │ • KB cache       │              │ │
│  └─────────────────┴──────────────────┴──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Performance

| Operation | First Request | Cached Request | Speedup |
|-----------|---------------|----------------|---------|
| Get Ticket | ~17s | ~1.3s | **13x** |
| List Tickets | ~17s | ~1.5s | **11x** |

Cookie lifetime: ~8 hours (sliding window — refreshed on each successful API call). A 5-minute auth cooldown prevents account lockout from rapid re-authentication attempts.

## Development

### Testing

```bash
# Unit tests (fast, no credentials needed)
uv run pytest tests/unit/ -v

# E2E tests (requires credentials)
export ORI_PORTAL_USERNAME="your@email.com"
export ORI_PORTAL_PASSWORD="yourpassword"
uv run pytest tests/e2e/ -v

# All tests
uv run pytest -v
```

### Linting

```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
```

## Project Structure

```
dc-support-mcp/
├── src/dc_support_mcp/
│   ├── __init__.py
│   ├── mcp_server.py          # FastMCP server + 6 tools
│   ├── vendor_handler.py      # Abstract base class
│   ├── constants.py           # Portal URLs, timeouts
│   ├── decorators.py          # verbose_log, validate_ticket_id
│   ├── types.py               # TypedDicts for tickets/comments
│   ├── validation.py          # ValidationError
│   ├── setup.py               # Playwright install helper
│   └── vendors/
│       ├── vendor_registry.py # Lazy init + caching
│       ├── ori.py             # ORI Industries (Atlassian)
│       └── iren.py            # IREN (Freshdesk web scraping)
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_vendor_handler.py
│   │   ├── test_vendor_registry.py
│   │   ├── test_session_manager.py
│   │   ├── test_new_features.py
│   │   └── test_iren_handler.py
│   └── e2e/
│       └── test_ori_portal.py
├── docs/                      # Architecture & vendor guides
├── pyproject.toml
├── Dockerfile
└── README.md
```

## Supported Vendors

| Vendor | Status | Auth | Operations | Notes |
|--------|--------|------|------------|-------|
| **ORI Industries** | Production | Playwright + Cookies | Get, List, Create, Comment | Atlassian Service Desk, REST API |
| **IREN** | Needs tuning | Playwright + Cookies | Get, List, KB Search | Freshdesk, CSS selectors need customization |

### Adding a New Vendor

1. Create `src/dc_support_mcp/vendors/yourvendor.py` inheriting from `VendorHandler`
2. Implement `authenticate()`, `get_ticket()`, `list_tickets()`
3. Register in `mcp_server.py`: `_registry.register("yourvendor", YourHandler)`
4. Set env vars: `YOURVENDOR_PORTAL_USERNAME`, `YOURVENDOR_PORTAL_PASSWORD`

See [docs/VENDOR_ARCHITECTURE.md](docs/VENDOR_ARCHITECTURE.md) for the full guide.

## License

Apache-2.0
