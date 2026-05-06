#!/usr/bin/env python3
"""Seed the memory system with existing workspace data.

Run from the agent-memory directory:
    python -m scripts.seed_workspace
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

import os

WORKSPACE = Path(os.environ.get("WORKSPACE_PATH", "/workspaces/together"))

SEED_SOURCES: list[dict] = [
    {
        "group_id": "incidents",
        "description": "On-call incident notes and resolutions",
        "paths": [
            WORKSPACE / "oncallmarch10to16-2026",
            WORKSPACE / "aiagentdev" / "oncallmarch10to16-2026",
            WORKSPACE / "OnCall-March10to16-2026.md",
            WORKSPACE / "OnCall-March3to10-2026.md",
        ],
    },
    {
        "group_id": "infrastructure",
        "description": "Infrastructure knowledge and procedures",
        "paths": [
            WORKSPACE / "iren300bringup",
            WORKSPACE / "orirebuild",
            WORKSPACE / "HARDWARE_VARIANT_DOCUMENTATION_SUMMARY.md",
            WORKSPACE / "CLUSTER_BRINGDOWNS_NOTE.md",
            WORKSPACE / "ROLE_PATCHES.md",
            WORKSPACE / "docstoadd",
        ],
    },
    {
        "group_id": "design",
        "description": "Design documents and architecture",
        "paths": [
            WORKSPACE / "aiagentdev" / "incident-triage-agent-design.md",
        ],
    },
]

INGESTABLE_EXTENSIONS = {".md", ".txt"}

# Patterns that look like actual secret values (not just names/references)
import re

_SECRET_PATTERNS = re.compile(
    r"(?:"
    r"sk-ant-api\S+"  # Anthropic keys
    r"|sk-[a-zA-Z0-9]{20,}"  # OpenAI keys
    r"|ghp_[a-zA-Z0-9]{30,}"  # GitHub PATs
    r"|tgp_v1_\S+"  # Together keys
    r"|Bearer [a-zA-Z0-9_\-\.]{20,}"  # Bearer tokens
    r"|[a-f0-9]{40,}"  # Long hex tokens (40+ chars)
    r"|eyJ[a-zA-Z0-9_\-]{50,}"  # JWTs
    r")",
    re.IGNORECASE,
)


def _sanitize(content: str) -> str:
    """Redact lines containing actual secret values."""
    lines = content.split("\n")
    clean = []
    redacted = 0
    for line in lines:
        if _SECRET_PATTERNS.search(line):
            clean.append("[REDACTED — line contained potential secret]")
            redacted += 1
        else:
            clean.append(line)
    if redacted:
        console.print(f"    [yellow]⚠ Redacted {redacted} line(s) with potential secrets[/yellow]")
    return "\n".join(clean)


def _collect_files(root: Path) -> list[Path]:
    """Recursively collect markdown/text files from a path."""
    if root.is_file():
        return [root] if root.suffix in INGESTABLE_EXTENSIONS else []
    if root.is_dir():
        return sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix in INGESTABLE_EXTENSIONS
        )
    return []


async def seed() -> dict:
    """Run the full seed process and return summary stats."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_memory.backend import get_backend

    backend = await get_backend()

    totals = {"ok": 0, "skipped": 0, "error": 0}
    group_results: dict[str, list[dict]] = {}

    for source in SEED_SOURCES:
        gid: str = source["group_id"]
        desc: str = source["description"]
        group_results[gid] = []

        console.print(f"\n[bold blue]▶ {desc}[/bold blue]  (group: [yellow]{gid}[/yellow])")

        files: list[Path] = []
        for p in source["paths"]:
            if not p.exists():
                console.print(f"  [dim]skip (not found): {p}[/dim]")
                totals["skipped"] += 1
                continue
            files.extend(_collect_files(p))

        if not files:
            console.print("  [dim]No files to ingest.[/dim]")
            continue

        for fp in files:
            rel = fp.relative_to(WORKSPACE) if fp.is_relative_to(WORKSPACE) else fp
            try:
                raw = fp.read_text(encoding="utf-8", errors="replace")
                if not raw.strip():
                    console.print(f"  [dim]skip (empty): {rel}[/dim]")
                    totals["skipped"] += 1
                    continue

                content = _sanitize(raw)
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=UTC)
                episode_name = fp.stem.replace("-", " ").replace("_", " ")

                await backend.add_episode(
                    name=episode_name,
                    body=content,
                    source="text",
                    source_description=f"Seed: {desc} — {rel}",
                    group_id=gid,
                    reference_time=mtime,
                )
                console.print(f"  [green]✓[/green] {rel}")
                totals["ok"] += 1
                group_results[gid].append({"file": str(rel), "status": "ok"})

            except Exception as exc:
                console.print(f"  [red]✗[/red] {rel}: {exc}")
                totals["error"] += 1
                group_results[gid].append({"file": str(rel), "status": "error", "error": str(exc)})

    return {"totals": totals, "groups": group_results}


def _print_summary(stats: dict) -> None:
    t = stats["totals"]

    console.print()
    table = Table(title="Seed Summary")
    table.add_column("Group", style="cyan")
    table.add_column("Files", style="green", justify="right")
    table.add_column("Errors", style="red", justify="right")

    for gid, entries in stats["groups"].items():
        ok = sum(1 for e in entries if e["status"] == "ok")
        err = sum(1 for e in entries if e["status"] == "error")
        table.add_row(gid, str(ok), str(err))

    console.print(table)
    console.print(
        f"\n[bold]Total:[/bold] {t['ok']} ingested, {t['skipped']} skipped, {t['error']} errors"
    )


def main() -> None:
    console.print("[bold]Seeding agent memory from workspace data …[/bold]")
    stats = asyncio.run(seed())
    _print_summary(stats)

    if stats["totals"]["error"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
