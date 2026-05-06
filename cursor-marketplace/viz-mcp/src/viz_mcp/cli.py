"""CLI for viz-mcp -- render reports, charts, and tables from the command line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from mcp_common.agent_remediation import install_cli_exception_handler

app = typer.Typer(
    name="viz-cli",
    help="Render reports, charts, and tables to HTML, PNG, clipboard, or Streamlit.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/viz-mcp")


@app.command()
def clipboard(
    text: Annotated[str, typer.Argument(help="Markdown text to copy as rich HTML")],
) -> None:
    """Copy markdown as rich HTML to the clipboard for Slack paste."""
    from viz_mcp.render import to_rich_clipboard

    success = to_rich_clipboard(text)
    if success:
        typer.echo("Rich HTML copied to clipboard.")
    else:
        typer.echo("Error: pandoc and/or xclip not installed.", err=True)
        raise typer.Exit(1)


@app.command()
def table(
    input: Annotated[Path, typer.Option("--input", "-i", help="CSV or JSON file with data")],
    title: Annotated[str | None, typer.Option("--title", "-t", help="Table title")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output HTML file (default: stdout)")
    ] = None,
) -> None:
    """Format a CSV/JSON file as a styled HTML table."""
    import pandas as pd

    from viz_mcp.render import df_to_html_table

    if input.suffix == ".csv":
        df = pd.read_csv(input)
    elif input.suffix == ".json":
        df = pd.read_json(input)
    else:
        typer.echo(f"Unsupported file type: {input.suffix} (use .csv or .json)", err=True)
        raise typer.Exit(1)

    html = df_to_html_table(df, title=title)

    if output:
        output.write_text(html, encoding="utf-8")
        typer.echo(f"Table written to {output}")
    else:
        typer.echo(html)


@app.command()
def png(
    input: Annotated[Path, typer.Option("--input", "-i", help="HTML file to render")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output PNG file path")],
    width: Annotated[int, typer.Option("--width", "-w", help="Image width")] = 1200,
    height: Annotated[int, typer.Option("--height", help="Image height")] = 800,
) -> None:
    """Render an HTML file to a PNG image."""
    from viz_mcp.render import to_png

    html_content = input.read_text(encoding="utf-8")
    try:
        path = to_png(html_content, output, width=width, height=height)
        typer.echo(f"PNG rendered to {path}")
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e


@app.command()
def html(
    input: Annotated[Path, typer.Option("--input", "-i", help="JSON file with sections array")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output HTML file path")],
    title: Annotated[str, typer.Option("--title", "-t", help="Report title")] = "Report",
) -> None:
    """Generate a self-contained interactive HTML report from a sections JSON file."""
    from viz_mcp.render import to_html

    sections = json.loads(input.read_text(encoding="utf-8"))
    path = to_html(sections, output, title=title)
    typer.echo(f"HTML report rendered to {path}")


@app.command()
def streamlit(
    input: Annotated[Path, typer.Option("--input", "-i", help="JSON file with sections array")],
    title: Annotated[str, typer.Option("--title", "-t", help="Dashboard title")] = "Report",
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = 8501,
) -> None:
    """Launch a Streamlit dashboard from a sections JSON file."""
    from viz_mcp.render import to_streamlit

    sections = json.loads(input.read_text(encoding="utf-8"))
    typer.echo(f"Starting Streamlit on http://localhost:{port}")
    proc = to_streamlit(sections, title=title, port=port, open_browser=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        typer.echo("\nStreamlit stopped.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
