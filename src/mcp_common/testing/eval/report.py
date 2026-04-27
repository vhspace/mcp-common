"""CLI entry point for analyzing eval logs and filing issues.

Usage::

    python -m mcp_common.testing.eval --log-dir ./logs/ --dry-run
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues --repo-prefix myorg
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues --auto-fix
    python -m mcp_common.testing.eval --log-dir ./logs/ --auto-fix --agent cursor
"""

from __future__ import annotations

from pathlib import Path

import typer

from mcp_common.testing.eval.analyzer import EvalFailure, analyze_eval_dir
from mcp_common.testing.eval.issue_filer import deduplicate, file_issues
from mcp_common.testing.eval.remediate import remediate_batch

app = typer.Typer(help="Analyze Inspect AI eval logs and optionally file GitHub issues.")


def _failure_key(failure: EvalFailure) -> str:
    """Build a stable key for matching a failure to its filed issue URL."""
    return f"{failure.server}|{failure.scenario}"


@app.command()
def report(
    log_dir: Path = typer.Option(..., "--log-dir", help="Directory containing .eval files"),  # noqa: B008
    dry_run: bool = typer.Option(True, "--dry-run/--create-issues", help="Preview without filing"),
    auto_fix: bool = typer.Option(
        False, "--auto-fix", help="Dispatch agent to fix failures after filing issues"
    ),
    agent_backend: str = typer.Option("claude", "--agent", help="Agent backend: claude or cursor"),
    repo_prefix: str = typer.Option("vhspace", "--repo-prefix", help="GitHub org prefix"),
    workspace_root: Path = typer.Option(  # noqa: B008
        "/workspaces/together", "--workspace", help="Workspace root path"
    ),
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

    if auto_fix:
        typer.echo("\n--- Remediation ---")
        issue_url_map: dict[str, str] = {}
        if not dry_run and urls:
            for failure, url in zip(unique, urls, strict=False):
                issue_url_map[_failure_key(failure)] = url
        elif dry_run:
            for failure in unique:
                issue_url_map[_failure_key(failure)] = (
                    f"https://github.com/{repo_prefix}/{failure.server}/issues/DRAFT"
                )

        pr_urls = remediate_batch(
            unique,
            issue_url_map,
            workspace_root=workspace_root,
            agent_backend=agent_backend,
            dry_run=dry_run,
        )
        if pr_urls:
            typer.echo(f"\nOpened {len(pr_urls)} PR(s):")
            for pr_url in pr_urls:
                typer.echo(f"  {pr_url}")
        elif not dry_run:
            typer.echo("\nNo PRs were opened.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
