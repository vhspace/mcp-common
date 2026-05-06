"""Typer CLI wrapper for the agent long-term memory system."""

import asyncio
import json
import warnings
from datetime import UTC, datetime
from pathlib import Path

import typer
from mcp_common.agent_remediation import install_cli_exception_handler
from rich.console import Console
from rich.table import Table

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"graphiti_core")

app = typer.Typer(name="mem", help="Agent long-term memory CLI", no_args_is_help=True)
install_cli_exception_handler(app, project_repo="vhspace/agent-memory")
console = Console(stderr=True)
out = Console()

_backend = None


def _run(coro):
    """Run async code from sync typer commands."""
    return asyncio.run(coro)


async def _get_backend():
    global _backend
    if _backend is None:
        from .backend import get_backend

        _backend = await get_backend()
    return _backend


def _emit(data, *, as_json: bool):
    """Unified output: raw JSON to stdout or pretty-print via rich."""
    if as_json:
        out.print_json(json.dumps(data, default=str))


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
@app.command()
def groups(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """List all memory groups (namespaces) in the knowledge graph."""

    async def _groups():
        backend = await _get_backend()
        return await backend.get_groups()

    results = _run(_groups())

    if as_json:
        _emit(results, as_json=True)
        return

    if not results:
        console.print("[dim]No groups found.[/dim]")
        return

    table = Table(title="Memory Groups")
    table.add_column("Group", style="cyan")
    table.add_column("Episodes", style="green", justify="right")
    table.add_column("Last Active", style="yellow")

    for g in results:
        table.add_row(
            str(g.get("group_id", "")),
            str(g.get("episode_count", 0)),
            str(g.get("last_active", "")),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    group_id: str | None = typer.Option(None, "--group-id", "-g", help="Filter by group"),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Max results"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Search agent memory for facts and relationships. Searches all groups by default."""

    async def _search():
        backend = await _get_backend()
        return await backend.search_facts(
            query=query,
            group_ids=[group_id] if group_id else None,
            max_facts=max_results,
        )

    try:
        results = _run(_search())
    except Exception as e:
        console.print(f"[red]Search error:[/red] {e}")
        raise typer.Exit(code=1) from None

    if as_json:
        _emit(results, as_json=True)
        return

    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(title=f"Memory Search: {query}")
    table.add_column("Fact", style="cyan", no_wrap=False, max_width=60)
    table.add_column("From", style="green")
    table.add_column("To", style="green")
    table.add_column("Valid At", style="yellow")
    table.add_column("Status", style="red")

    for r in results:
        status = "superseded" if r.get("invalid_at") else "active"
        table.add_row(
            r.get("fact", ""),
            r.get("source_node", ""),
            r.get("target_node", ""),
            str(r.get("valid_at", "")),
            status,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------
@app.command()
def add(
    name: str = typer.Argument(..., help="Episode name"),
    body: str = typer.Argument(..., help="Episode content"),
    source: str = typer.Option("text", "--source", "-s", help="Source type: text|json|message"),
    description: str = typer.Option("", "--description", "-d", help="Source description"),
    group_id: str | None = typer.Option(None, "--group-id", "-g", help="Group ID"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Add a memory episode to the knowledge graph."""

    async def _add():
        backend = await _get_backend()
        return await backend.add_episode(
            name=name,
            body=body,
            source=source,
            source_description=description,
            group_id=group_id,
            reference_time=datetime.now(UTC),
        )

    result = _run(_add())

    if as_json:
        _emit(result, as_json=True)
        return

    console.print(f"[green]✓[/green] Episode added: [bold]{name}[/bold]")
    if result.get("uuid"):
        console.print(f"  UUID: {result['uuid']}")


# ---------------------------------------------------------------------------
# episodes
# ---------------------------------------------------------------------------
@app.command()
def episodes(
    group_id: str | None = typer.Option(None, "--group-id", "-g", help="Filter by group"),
    last_n: int = typer.Option(10, "--last-n", "-n", help="Number of recent episodes"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """List recent memory episodes."""

    async def _episodes():
        backend = await _get_backend()
        return await backend.get_episodes(group_id=group_id, last_n=last_n)

    results = _run(_episodes())

    if as_json:
        _emit(results, as_json=True)
        return

    if not results:
        console.print("[dim]No episodes found.[/dim]")
        return

    table = Table(title="Recent Episodes")
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="cyan", no_wrap=False, max_width=50)
    table.add_column("Source", style="green")
    table.add_column("Group", style="yellow")
    table.add_column("Created", style="magenta")

    for i, ep in enumerate(results, 1):
        table.add_row(
            str(i),
            ep.get("name", ""),
            str(ep.get("source", "")),
            ep.get("group_id", ""),
            str(ep.get("created_at", "")),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Check memory system health."""

    async def _status():
        backend = await _get_backend()
        return await backend.get_status()

    info = _run(_status())

    if as_json:
        _emit(info, as_json=True)
        return

    console.print("[bold]Memory System Status[/bold]")
    console.print()
    for key, value in info.items():
        label = key.replace("_", " ").title()
        console.print(f"  {label}: [cyan]{value}[/cyan]")


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------
@app.command()
def forget(
    query: str | None = typer.Argument(None, help="Search query to find facts to forget"),
    entity: str | None = typer.Option(
        None, "--entity", "-e", help="Remove all facts about this entity"
    ),
    group_id: str | None = typer.Option(None, "--group-id", "-g", help="Scope to a specific group"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would be removed without deleting"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Max facts to match"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Immediately remove incorrect or stale facts from memory.

    Find matching facts by search query or entity name and delete them.
    Defaults to interactive confirmation; use --yes to skip.

    \b
    Examples:
        mem forget "node X is in cluster Y" --group-id infrastructure
        mem forget --entity "research-common-h100-062" --group-id infrastructure
        mem forget "stale fact" --dry-run
    """
    if not query and not entity:
        console.print("[red]Error:[/red] Provide a search query or --entity name.")
        raise typer.Exit(code=1)

    async def _execute():
        backend = await _get_backend()
        if entity:
            facts = await backend.search_entity_facts(
                entity_name=entity,
                group_ids=[group_id] if group_id else None,
            )
        else:
            facts = await backend.search_facts(
                query=query,
                group_ids=[group_id] if group_id else None,
                max_facts=max_results,
            )

        if not facts:
            return {"facts": [], "deleted": 0}

        _print_facts_table(facts, dry_run=dry_run)

        if dry_run:
            return {"facts": facts, "deleted": 0, "dry_run": True}

        if not yes:
            confirmed = typer.confirm(f"Remove {len(facts)} fact(s)?")
            if not confirmed:
                return {"facts": facts, "deleted": 0, "aborted": True}

        uuids = [f["uuid"] for f in facts if f.get("uuid")]
        deleted = await backend.delete_facts_by_uuid(uuids)
        return {"facts": facts, "deleted": deleted, "uuids": uuids}

    try:
        result = _run(_execute())
    except Exception as e:
        console.print(f"[red]Search error:[/red] {e}")
        raise typer.Exit(code=1) from None

    facts = result.get("facts", [])
    if not facts:
        console.print("[dim]No matching facts found.[/dim]")
        return

    if result.get("dry_run"):
        console.print(f"\n[yellow]Dry-run:[/yellow] {len(facts)} fact(s) would be removed.")
        if as_json:
            _emit({"dry_run": True, "would_remove": len(facts), "facts": facts}, as_json=True)
        return

    if result.get("aborted"):
        console.print("[dim]Aborted.[/dim]")
        return

    if as_json:
        _emit(result, as_json=True)
        return

    console.print(
        f"\n[green]✓[/green] Removed [bold]{result['deleted']}[/bold] fact(s) from memory."
    )


def _print_facts_table(facts: list[dict], *, dry_run: bool = False) -> None:
    table = Table(title="Facts to forget" + (" [dry-run]" if dry_run else ""))
    table.add_column("#", style="dim", width=4)
    table.add_column("Fact", style="cyan", no_wrap=False, max_width=60)
    table.add_column("From", style="green")
    table.add_column("To", style="green")
    for i, f in enumerate(facts, 1):
        table.add_row(
            str(i),
            f.get("fact", ""),
            f.get("source_node", ""),
            f.get("target_node", ""),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# decay
# ---------------------------------------------------------------------------
@app.command()
def decay(
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Dry-run by default"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Run memory decay evaluation cycle."""

    async def _decay():
        from .decay import run_decay_cycle

        backend = await _get_backend()
        return await run_decay_cycle(backend)

    if dry_run:
        console.print("[yellow]Dry-run mode[/yellow] — no changes will be persisted.")

    result = _run(_decay())

    if as_json:
        _emit(result, as_json=True)
        return

    evaluated = result.get("evaluated", 0)
    pruned = result.get("pruned", 0)
    console.print("[bold]Decay cycle complete[/bold]")
    console.print(f"  Evaluated: [cyan]{evaluated}[/cyan] memories")
    console.print(f"  Pruned:    [red]{pruned}[/red] memories")
    if result.get("details"):
        for d in result["details"]:
            console.print(f"    - {d}")


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------
@app.command()
def promote(
    output_dir: str | None = typer.Option(None, "--output-dir", "-o", help="Cursor rules dir"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Promote consolidated memories to Cursor rule files."""

    async def _promote():
        from .decay import promote_memories_to_rules

        backend = await _get_backend()
        target = output_dir or backend.settings.rules_output_dir
        return await promote_memories_to_rules(backend, target)

    result = _run(_promote())

    if as_json:
        _emit(result, as_json=True)
        return

    promoted = result.get("promoted", 0)
    files = result.get("files", [])
    console.print("[bold]Promotion complete[/bold]")
    console.print(f"  Promoted: [green]{promoted}[/green] memories → rule files")
    for f in files:
        console.print(f"    [dim]{f}[/dim]")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
INGESTABLE_EXTENSIONS = {".md", ".mdc", ".txt"}


def _collect_files(root: Path, *, recursive: bool) -> list[Path]:
    """Collect ingestable files from a path."""
    if root.is_file():
        return [root] if root.suffix in INGESTABLE_EXTENSIONS else []

    if not root.is_dir():
        return []

    if recursive:
        return sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix in INGESTABLE_EXTENSIONS
        )
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix in INGESTABLE_EXTENSIONS)


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to file or directory to ingest"),
    group_id: str | None = typer.Option(None, "--group-id", "-g", help="Group ID"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output raw JSON"),
):
    """Ingest markdown/text files into the knowledge graph."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        console.print(f"[red]Error:[/red] path does not exist: {target}")
        raise typer.Exit(code=1)

    files = _collect_files(target, recursive=recursive)
    if not files:
        console.print("[yellow]No ingestable files found (.md, .mdc, .txt).[/yellow]")
        raise typer.Exit(code=0)

    async def _ingest():
        backend = await _get_backend()
        results = []
        for fp in files:
            content = fp.read_text(encoding="utf-8", errors="replace")
            mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=UTC)
            episode_name = fp.stem.replace("-", " ").replace("_", " ")

            try:
                result = await backend.add_episode(
                    name=episode_name,
                    body=content,
                    source="text",
                    source_description=f"Ingested from {fp}",
                    group_id=group_id,
                    reference_time=mtime,
                )
                results.append({"file": str(fp), "status": "ok", "result": result})
            except Exception as exc:
                results.append({"file": str(fp), "status": "error", "error": str(exc)})

        return results

    console.print(f"Ingesting [bold]{len(files)}[/bold] file(s) from [cyan]{target}[/cyan] ...")
    results = _run(_ingest())

    if as_json:
        _emit(results, as_json=True)
        return

    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")

    table = Table(title="Ingestion Results")
    table.add_column("File", style="cyan", no_wrap=False, max_width=70)
    table.add_column("Status", style="green")

    for r in results:
        status_str = "[green]✓[/green]" if r["status"] == "ok" else f"[red]✗ {r['error']}[/red]"
        table.add_row(r["file"], status_str)

    console.print(table)
    console.print(f"\n[bold]Done:[/bold] {ok} succeeded, {err} failed, {len(files)} total")


if __name__ == "__main__":
    app()
