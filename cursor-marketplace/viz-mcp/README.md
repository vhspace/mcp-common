[![CI](https://github.com/vhspace/viz-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/viz-mcp/actions/workflows/ci.yml)
[![Release](.github/badges/release.svg)](https://github.com/vhspace/viz-mcp/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

# viz-mcp

MCP server and CLI for data visualization -- render shareable reports, charts, and tables to HTML, PNG, clipboard, or Streamlit.

## Installation

```bash
pip install viz-mcp
```

## MCP Server

```bash
viz-mcp                    # stdio transport (default)
viz-mcp --transport http   # HTTP transport
```

## CLI

```bash
viz-cli clipboard "## Status\n- **Healthy**: 47/48"
viz-cli png --input data.html --output report.png
viz-cli html --input sections.json --output report.html --title "Weekly Report"
viz-cli streamlit --input sections.json --port 8501
viz-cli table --input data.csv --title "Node Health"
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `viz_clipboard` | Copy markdown as rich HTML to clipboard for Slack paste |
| `viz_png` | Render HTML to PNG image |
| `viz_html_report` | Generate self-contained interactive HTML report |
| `viz_streamlit` | Launch a Streamlit dashboard from sections data |
| `viz_table` | Format JSON data as a styled HTML table |

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src/
```
