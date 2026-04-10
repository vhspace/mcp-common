---
name: shareable-output
description: >-
  Render agent output (tables, charts, text) into Slack-friendly formats:
  rich clipboard paste, PNG images, self-contained interactive HTML files,
  or a live Streamlit dashboard. Use when the user asks to share output,
  create a report, render a dashboard, export results, or make output
  shareable in Slack.
---

# Shareable Output

## MCP Tools

If `viz-mcp` is running as an MCP server, use these tools directly:

| Tool | Description |
|------|-------------|
| `viz_clipboard` | Copy markdown as rich HTML to clipboard for Slack paste |
| `viz_table` | Format JSON records as a styled HTML table |
| `viz_png` | Render HTML to PNG image |
| `viz_html_report` | Generate self-contained interactive HTML report |
| `viz_streamlit` | Launch a Streamlit dashboard |

## CLI

```bash
viz-cli clipboard "## Status\n- **Healthy**: 47/48"
viz-cli table --input data.csv --title "Node Health"
viz-cli png --input data.html --output report.png
viz-cli html --input sections.json --output report.html --title "Weekly Report"
viz-cli streamlit --input sections.json --port 8501
```

## Python API

All functions live in `viz_mcp.render`.

### Clipboard (instant paste into Slack)

```python
from viz_mcp.render import to_rich_clipboard

to_rich_clipboard("## Status\n- **Healthy**: 47/48")
```

Converts markdown to HTML via `pandoc`, copies to clipboard via `xclip`.
Slack renders bold, italic, lists, links, code blocks. **Not tables** -- use PNG or HTML.

### PNG Image (drag-drop into Slack)

```python
from viz_mcp.render import to_png

to_png(html_string, "/tmp/report.png", width=1200, height=800)
```

Accepts Plotly figures, matplotlib figures, or HTML strings.

### HTML Table Helper

```python
from viz_mcp.render import df_to_html_table, records_to_html_table

html = df_to_html_table(df, title="GPU Utilization")
html = records_to_html_table([{"node": "a1", "status": "ok"}], title="Nodes")
```

### Self-contained HTML Report

```python
from viz_mcp.render import to_html

sections = [
    {"type": "text", "title": "Summary", "content": "<p>All healthy.</p>"},
    {"type": "table", "records": [...], "columns": [...], "title": "Nodes"},
    {"type": "chart", "figure": plotly_fig, "title": "Traffic"},
]
to_html(sections, "/tmp/report.html", title="Weekly Report")
```

### Streamlit Dashboard

```python
from viz_mcp.render import to_streamlit

proc = to_streamlit(sections, title="Dashboard", port=8501)
# proc.terminate() to stop
```

Same sections format as `to_html()`. Sharing options:
- **ngrok**: `ngrok http 8501`
- **Tailscale**: `tailscale funnel 8501`
- **Cursor port forwarding**: auto if in dev container

## Dependencies

System: `pandoc` and `xclip` for clipboard only.
