"""MCP server for viz-mcp -- data visualization and report rendering.

Exposes tools for rendering agent output to clipboard, PNG, HTML, and Streamlit.
"""

from __future__ import annotations

import json
import logging
import tempfile
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp_common import get_version, setup_logging, suppress_ssl_warnings
from mcp_common.agent_remediation import mcp_remediation_wrapper
from pydantic import Field

from viz_mcp.render import (
    records_to_html_table,
    to_html,
    to_png,
    to_rich_clipboard,
    to_streamlit,
)

logger = logging.getLogger("viz_mcp.server")

mcp = FastMCP(
    "viz-mcp",
    instructions=(
        "Data visualization MCP server. Use these tools to render agent output "
        "into shareable formats: clipboard paste, PNG images, HTML reports, "
        "or Streamlit dashboards."
    ),
)


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/viz-mcp")
def viz_clipboard(
    markdown_text: Annotated[str, Field(description="Markdown text to copy as rich HTML")],
) -> str:
    """Copy markdown as rich HTML to the system clipboard.

    When pasted into Slack, renders with bold, italic, lists, links, and code blocks.
    Tables are NOT supported in clipboard paste -- use viz_png or viz_html_report instead.
    Requires pandoc and xclip to be installed on the system.
    """
    success = to_rich_clipboard(markdown_text)
    if success:
        return "Rich HTML copied to clipboard. Paste into Slack for formatted output."
    raise ToolError(
        "Could not copy to clipboard. Ensure pandoc and xclip are installed. "
        "On Ubuntu/Debian: apt install pandoc xclip"
    )


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/viz-mcp")
def viz_table(
    records: Annotated[
        list[dict[str, Any]], Field(description="List of row dicts, e.g. [{col: val}, ...]")
    ],
    columns: Annotated[list[str] | None, Field(description="Column order (optional)")] = None,
    title: Annotated[str | None, Field(description="Table title (optional)")] = None,
) -> str:
    """Format JSON data as a styled HTML table.

    Returns an HTML string with inline CSS styling. Useful as input to
    viz_png (to render as image) or to embed in other HTML content.
    """
    return records_to_html_table(records, columns=columns, title=title)


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/viz-mcp")
def viz_png(
    html: Annotated[str, Field(description="HTML content to render as PNG")],
    output_path: Annotated[
        str | None, Field(description="Output file path (default: auto-generated in /tmp)")
    ] = None,
    width: Annotated[int, Field(description="Image width in pixels")] = 1200,
    height: Annotated[int, Field(description="Image height in pixels")] = 800,
) -> str:
    """Render HTML content to a PNG image file.

    Returns the path to the generated PNG. Drag-drop the file into Slack
    to share it -- it renders inline for everyone.
    """
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".png", prefix="viz_")
    try:
        path = to_png(html, output_path, width=width, height=height)
        return f"PNG rendered to {path}"
    except ValueError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/viz-mcp")
def viz_html_report(
    sections: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "List of section dicts. Each has 'type' (text|table|chart) plus: "
                "text: {content: str}, table: {records: [...], columns: [...]}, "
                "chart: {figure_json: str}. All can have optional 'title'."
            )
        ),
    ],
    title: Annotated[str, Field(description="Report title")] = "Report",
    output_path: Annotated[
        str | None, Field(description="Output file path (default: auto-generated in /tmp)")
    ] = None,
) -> str:
    """Generate a self-contained interactive HTML report.

    The output file includes embedded Plotly JS for interactive charts and
    styled tables. It can be opened offline in any browser. Attach as a file
    in Slack to share.
    """
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".html", prefix="viz_report_")
    path = to_html(sections, output_path, title=title)
    return f"HTML report rendered to {path}"


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/viz-mcp")
def viz_streamlit(
    sections_json: Annotated[
        str,
        Field(
            description=(
                "JSON string of sections array. Same format as viz_html_report sections, "
                "but as a JSON string."
            )
        ),
    ],
    title: Annotated[str, Field(description="Dashboard title")] = "Report",
    port: Annotated[int, Field(description="Server port")] = 8501,
) -> str:
    """Launch a Streamlit dashboard from sections data.

    Starts a Streamlit server as a background process. Share the URL with
    others via Cursor port forwarding, ngrok, or Tailscale funnel.
    Returns the URL where the dashboard is accessible.
    """
    sections = json.loads(sections_json)
    to_streamlit(sections, title=title, port=port, open_browser=False)
    return f"Streamlit dashboard launched at http://localhost:{port}"


def create_mcp_app() -> FastMCP:
    """Create and return the configured FastMCP app (for testing)."""
    return mcp


def main() -> None:
    suppress_ssl_warnings()
    setup_logging(level="INFO", name="viz_mcp")
    version = get_version("viz-mcp")
    logger.info("Starting viz-mcp server v%s", version)
    mcp.run()


if __name__ == "__main__":
    main()
