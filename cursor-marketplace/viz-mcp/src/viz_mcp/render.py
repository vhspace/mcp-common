"""Core rendering engine for shareable output.

Supports four output formats:
- Rich clipboard (markdown -> HTML -> clipboard via pandoc + xclip)
- PNG image (charts/tables -> static image)
- Self-contained HTML (interactive Plotly + styled tables)
- Streamlit (interactive local dashboard)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
import plotly.io as pio

from viz_mcp.templates import get_report_template

if TYPE_CHECKING:
    import pandas as pd


def to_rich_clipboard(markdown_text: str) -> bool:
    """Convert markdown to HTML and copy to clipboard as rich text.

    Uses pandoc to convert markdown -> HTML, then xclip to put it on
    the clipboard as text/html. When pasted into Slack, renders with
    formatting (bold, italic, lists, links, code blocks).

    Tables are NOT supported -- Slack strips HTML tables from paste.

    Returns True if successful, False if pandoc/xclip not available.
    """
    if not shutil.which("pandoc") or not shutil.which("xclip"):
        return False

    pandoc = subprocess.run(
        ["pandoc", "-f", "gfm", "-t", "html"],
        input=markdown_text,
        capture_output=True,
        text=True,
        check=True,
    )

    subprocess.run(
        ["xclip", "-sel", "clipboard", "-t", "text/html"],
        input=pandoc.stdout,
        text=True,
        check=True,
    )
    return True


def to_png(
    content: str | object,
    output_path: str | Path,
    *,
    width: int = 1200,
    height: int = 800,
) -> Path:
    """Render content to a PNG image file.

    Args:
        content: Either an HTML string, a Plotly figure object, or
                 a matplotlib figure object.
        output_path: Where to save the PNG file.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Path to the generated PNG file.
    """
    output_path = Path(output_path)

    try:
        import plotly.graph_objects as go

        if isinstance(content, go.Figure):
            content.write_image(str(output_path), width=width, height=height, scale=2)
            return output_path
    except ImportError:
        pass

    try:
        import matplotlib.figure

        if isinstance(content, matplotlib.figure.Figure):
            content.savefig(str(output_path), dpi=150, bbox_inches="tight")
            return output_path
    except ImportError:
        pass

    if isinstance(content, str):
        try:
            from html2image import Html2Image

            hti = Html2Image(size=(width, height))
            hti.screenshot(html_str=content, save_as=output_path.name)
            generated = Path(output_path.name)
            if generated.exists() and generated != output_path:
                generated.rename(output_path)
            return output_path
        except ImportError:
            pass

        if shutil.which("wkhtmltoimage"):
            with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
                f.write(content)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["wkhtmltoimage", "--width", str(width), tmp_path, str(output_path)],
                    check=True,
                    capture_output=True,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            return output_path

    raise ValueError(
        f"Cannot render {type(content).__name__} to PNG. "
        "Provide an HTML string, Plotly figure, or matplotlib figure. "
        "For HTML, install html2image or wkhtmltoimage."
    )


def df_to_html_table(df: pd.DataFrame, title: str | None = None) -> str:
    """Convert a pandas DataFrame to a styled HTML table string."""
    from tabulate import tabulate as _tabulate

    table_html = _tabulate(df, headers="keys", tablefmt="html", showindex=False)

    css = """\
<style>
table { border-collapse: collapse; width: 100%; \
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; }
th { background-color: #1a1a2e; color: #e0e0e0; padding: 10px 14px; text-align: left; \
font-weight: 600; border-bottom: 2px solid #16213e; }
td { padding: 8px 14px; border-bottom: 1px solid #e8e8e8; color: #2d2d2d; }
tr:nth-child(even) { background-color: #f8f9fa; }
tr:hover { background-color: #e8f4f8; }
</style>"""
    title_html = (
        f"<h2 style='font-family: sans-serif; color: #1a1a2e;'>{title}</h2>" if title else ""
    )
    return f"{css}{title_html}{table_html}"


def records_to_html_table(
    records: list[dict[str, Any]],
    columns: list[str] | None = None,
    title: str | None = None,
) -> str:
    """Convert JSON records to a styled HTML table string.

    This is the JSON-friendly equivalent of df_to_html_table, used by MCP tools
    and CLI where DataFrames aren't passed directly.
    """
    import pandas as pd

    df = pd.DataFrame(records, columns=columns)
    return df_to_html_table(df, title=title)


def to_html(
    sections: list[dict[str, Any]],
    output_path: str | Path,
    *,
    title: str = "Report",
) -> Path:
    """Render a multi-section report to a self-contained HTML file.

    Each section dict can have:
      - type: "text" | "table" | "chart"
      - For "text": content (str)
      - For "table": df (DataFrame) or records (list[dict]) + columns (list[str])
      - For "chart": figure (Plotly figure object) or figure_json (str)

    Returns:
        Path to the generated HTML file.
    """
    import pandas as pd

    output_path = Path(output_path)
    template = jinja2.Template(get_report_template())

    rendered_sections: list[dict[str, Any]] = []
    for section in sections:
        sec_type = section.get("type", "text")
        if sec_type == "text":
            rendered_sections.append(
                {
                    "type": "text",
                    "content": section["content"],
                    "title": section.get("title"),
                }
            )
        elif sec_type == "table":
            if "df" in section:
                html = df_to_html_table(section["df"], title=section.get("title"))
            else:
                df = pd.DataFrame(section["records"], columns=section.get("columns"))
                html = df_to_html_table(df, title=section.get("title"))
            rendered_sections.append({"type": "table", "html": html})
        elif sec_type == "chart":
            if "figure" in section:
                chart_html = pio.to_html(section["figure"], full_html=False, include_plotlyjs=False)
            else:
                fig = pio.from_json(section["figure_json"])
                chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
            rendered_sections.append(
                {
                    "type": "chart",
                    "html": chart_html,
                    "title": section.get("title"),
                }
            )

    html = template.render(title=title, sections=rendered_sections)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def to_streamlit(
    sections: list[dict[str, Any]],
    *,
    title: str = "Report",
    port: int = 8501,
    host: str = "0.0.0.0",
    open_browser: bool = True,
) -> subprocess.Popen:
    """Launch a Streamlit dashboard from sections data.

    Takes the same sections format as to_html(). Generates a temporary
    Streamlit app and starts it as a background subprocess.

    Returns:
        The subprocess.Popen handle. Call .terminate() to stop the server.
    """
    data_file = Path(tempfile.mktemp(suffix=".json", prefix="viz_data_"))
    app_file = Path(tempfile.mktemp(suffix=".py", prefix="viz_app_"))

    serialised = _serialise_sections(sections)
    data_file.write_text(json.dumps(serialised, default=str), encoding="utf-8")
    app_file.write_text(_generate_streamlit_app(data_file, title), encoding="utf-8")

    cmd = [
        "streamlit",
        "run",
        str(app_file),
        "--server.port",
        str(port),
        "--server.address",
        host,
        "--server.headless",
        str(not open_browser).lower(),
    ]

    return subprocess.Popen(cmd)


def _serialise_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert sections to JSON-safe dicts, serialising Plotly figures and DataFrames."""
    out: list[dict[str, Any]] = []
    for section in sections:
        sec_type = section.get("type", "text")
        if sec_type == "text":
            out.append(
                {
                    "type": "text",
                    "content": section["content"],
                    "title": section.get("title"),
                }
            )
        elif sec_type == "table":
            if "df" in section:
                df = section["df"]
                out.append(
                    {
                        "type": "table",
                        "records": df.to_dict(orient="records"),
                        "columns": list(df.columns),
                        "title": section.get("title"),
                    }
                )
            else:
                out.append(
                    {
                        "type": "table",
                        "records": section["records"],
                        "columns": section.get("columns", []),
                        "title": section.get("title"),
                    }
                )
        elif sec_type == "chart":
            if "figure" in section:
                out.append(
                    {
                        "type": "chart",
                        "figure_json": section["figure"].to_json(),
                        "title": section.get("title"),
                    }
                )
            else:
                out.append(
                    {
                        "type": "chart",
                        "figure_json": section["figure_json"],
                        "title": section.get("title"),
                    }
                )
    return out


def _generate_streamlit_app(data_path: Path, title: str) -> str:
    """Return the source code for a generated Streamlit app."""
    return f"""\
import json
import pandas as pd
import plotly.io as pio
import streamlit as st

st.set_page_config(page_title={title!r}, layout="wide")
st.title({title!r})

with open({str(data_path)!r}, encoding="utf-8") as _f:
    _sections = json.load(_f)

for _sec in _sections:
    _type = _sec.get("type", "text")
    _title = _sec.get("title")

    if _type == "text":
        if _title:
            st.header(_title)
        st.markdown(_sec["content"], unsafe_allow_html=True)

    elif _type == "table":
        if _title:
            st.header(_title)
        _df = pd.DataFrame(_sec["records"], columns=_sec["columns"])
        st.dataframe(_df, use_container_width=True)

    elif _type == "chart":
        if _title:
            st.header(_title)
        _fig = pio.from_json(_sec["figure_json"])
        st.plotly_chart(_fig, use_container_width=True)
"""
