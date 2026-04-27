"""CLI entry point for analyzing eval logs and filing issues.

Usage::

    python -m mcp_common.testing.eval --log-dir ./logs/ --dry-run
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues --repo-prefix myorg
"""

from __future__ import annotations

from pathlib import Path

import typer

from mcp_common.testing.eval.analyzer import analyze_eval_dir
from mcp_common.testing.eval.issue_filer import deduplicate, file_issues

app = typer.Typer(help="Analyze Inspect AI eval logs and optionally file GitHub issues.")


@app.command()
def report(
    log_dir: Path = typer.Option(..., "--log-dir", help="Directory containing .eval files"),  # noqa: B008
    dry_run: bool = typer.Option(True, "--dry-run/--create-issues", help="Preview without filing"),
    repo_prefix: str = typer.Option("vhspace", "--repo-prefix", help="GitHub org prefix"),
) -> None:
    """Analyze eval logs and report or file issues for failures."""
    if not log_dir.is_dir():
        typer.echo(f"Error: {log_dir} is not a directory", err=True)
        raise typer.Exit(1)

    failures = analyze_eval_dir(log_dir)
    if not failures:
        typer.echo("No failures found.")
        raise typer.Exit(0)

    typer.echo(f"Found {len(failures)} failure(s) across eval logs.")

    unique = deduplicate(failures, repo_prefix=repo_prefix)
    typer.echo(f"After deduplication: {len(unique)} unique failure(s).")

    if not unique:
        typer.echo("All failures already have open issues. Nothing to file.")
        raise typer.Exit(0)

    urls = file_issues(unique, dry_run=dry_run, repo_prefix=repo_prefix)

    if dry_run:
        typer.echo(f"\nDry run complete. {len(unique)} issue(s) would be filed.")
    else:
        typer.echo(f"\nFiled {len(urls)} issue(s).")
        for url in urls:
            typer.echo(f"  {url}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
