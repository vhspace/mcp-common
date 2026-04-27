"""
CLI entry point for mcp-plugin-gen.

Usage:
    mcp-plugin-gen                  # generate all platforms
    mcp-plugin-gen --platform cursor
    mcp-plugin-gen --platform claude
    mcp-plugin-gen --dry-run        # show what would be generated
    mcp-plugin-gen --init           # create a starter mcp-plugin.toml
    mcp-plugin-gen registry-entry . # emit .claude-plugin/registry-entry.json
    mcp-plugin-gen aggregate-marketplace ./entries ./marketplace.json
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import typer

from mcp_common.marketplace_builder import build_all as build_marketplace_all
from mcp_common.plugin_gen import (
    aggregate_marketplace_entries,
    generate_agents_md,
    generate_all,
    generate_claude,
    generate_cursor,
    generate_cursor_rule,
    generate_mcp_json,
    generate_opencode,
    generate_openhands,
    generate_registry_entry,
    load_config,
)

app = typer.Typer(
    name="mcp-plugin-gen",
    help="Generate platform-specific plugin configs from mcp-plugin.toml.",
    no_args_is_help=False,
)

GENERATORS = {
    "cursor": generate_cursor,
    "cursor-rule": generate_cursor_rule,
    "claude": generate_claude,
    "mcp-json": generate_mcp_json,
    "opencode": generate_opencode,
    "openhands": generate_openhands,
    "agents-md": generate_agents_md,
}

ENV_REF_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")

STARTER_TOML = """# MCP Plugin Config — single source of truth for all platforms.
# Run `mcp-plugin-gen` to produce Cursor, Claude Code, OpenCode, etc. configs.
# Version is sourced from pyproject.toml [project].version.

name = "{name}"
description = "{description}"
repository = "https://github.com/vhspace/{name}"
license = "Apache-2.0"
keywords = ["mcp", "infrastructure"]

[author]
name = "Together AI"

[server]
command = "uvx"
args = ["--from", "{name}", "{name}"]

[server.env]
# Add required env vars here, e.g.:
# MY_URL = "${{MY_URL}}"
# MY_TOKEN = "${{MY_TOKEN}}"

# Uncomment to add a companion CLI tool:
# [cli]
# name = "{cli_name}"
# entry_point = "{pkg}.cli:main"
# description = "Query {name} from the command line"

# Optional: private Claude marketplace metadata for registry-entry.json
# [marketplace]
# categories = ["infrastructure", "operations"]
# tags = ["mcp", "private", "claude"]

# Skills — list each SKILL.md source path
# [[skills]]
# name = "{name}-usage"
# description = "Use when ..."
# path = "skills/{name}-usage/SKILL.md"

# Rules — always-apply agent rules
# [[rules]]
# name = "{name}-conventions"
# path = "rules/{name}-conventions.mdc"

# Hooks — auto-setup CLI on first session
# [[hooks]]
# event = "SessionStart"
# script = "hooks/setup-cli"
# async = true
"""


@app.command()
def generate(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to the repo root (default: current directory)"
    ),
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Generate for specific platform only"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be generated"),
) -> None:
    """Generate platform-specific configs from mcp-plugin.toml."""
    repo_root = repo_root.resolve()

    try:
        cfg = load_config(repo_root)
    except FileNotFoundError:
        typer.echo(f"Error: No mcp-plugin.toml found in {repo_root}", err=True)
        typer.echo("Run `mcp-plugin-gen init` to create one.", err=True)
        raise typer.Exit(1) from None

    if platform:
        if platform not in GENERATORS:
            typer.echo(
                f"Error: Unknown platform '{platform}'. Choose from: {', '.join(GENERATORS)}",
                err=True,
            )
            raise typer.Exit(1)
        results = {platform: GENERATORS[platform](cfg, repo_root) if not dry_run else []}
    else:
        if dry_run:
            results = {p: [] for p in GENERATORS}
        else:
            results = generate_all(cfg, repo_root)

    if dry_run:
        typer.echo(f"Would generate from: {repo_root / 'mcp-plugin.toml'}")
        typer.echo(f"Plugin: {cfg.name} v{cfg.version}")
        typer.echo(f"Server: {cfg.server.command} {' '.join(cfg.server.args)}")
        if cfg.cli:
            typer.echo(f"CLI: {cfg.cli.name}")
        typer.echo(f"Skills: {len(cfg.skills)}")
        typer.echo(f"Hooks: {len(cfg.hooks)}")
        typer.echo(f"Rules: {len(cfg.rules)}")
        typer.echo(f"Platforms: {', '.join(GENERATORS)}")
        return

    total = 0
    for plat, files in results.items():
        typer.echo(f"\n  {plat}:")
        for f in files:
            typer.echo(f"    {f}")
            total += 1

    typer.echo(f"\n  Generated {total} files for {cfg.name} v{cfg.version}")


@app.command()
def init(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to the repo root (default: current directory)"
    ),
) -> None:
    """Create a starter mcp-plugin.toml in the current directory."""
    target = repo_root.resolve() / "mcp-plugin.toml"
    if target.exists():
        typer.echo(f"mcp-plugin.toml already exists at {target}")
        raise typer.Exit(1)

    dir_name = repo_root.resolve().name.removesuffix("-server").removesuffix("-mcp")
    pkg_name = dir_name.replace("-", "_")
    name = f"{dir_name}-mcp" if not dir_name.endswith("mcp") else dir_name
    cli_name = f"{dir_name}-cli" if not dir_name.endswith("cli") else dir_name

    content = STARTER_TOML.format(
        name=name,
        description=f"MCP server for {dir_name}",
        cli_name=cli_name,
        pkg=f"{pkg_name}_mcp",
    )

    with open(target, "w") as f:
        f.write(content)

    typer.echo(f"Created {target}")

    precommit_path = repo_root.resolve() / ".pre-commit-config.yaml"
    hook_block = """  - repo: https://github.com/vhspace/mcp-common
    rev: v0.7.0
    hooks:
      - id: mcp-plugin-gen
"""
    if precommit_path.exists():
        existing = precommit_path.read_text()
        if "mcp-plugin-gen" not in existing:
            with open(precommit_path, "a") as f:
                f.write(hook_block)
            typer.echo(f"Added mcp-plugin-gen hook to {precommit_path}")
        else:
            typer.echo("Pre-commit hook already configured.")
    else:
        with open(precommit_path, "w") as f:
            f.write(f"repos:\n{hook_block}")
        typer.echo(f"Created {precommit_path} with mcp-plugin-gen hook.")

    typer.echo(
        "Set pyproject.toml [project].version, edit mcp-plugin.toml, then run `mcp-plugin-gen generate .`"
    )


@app.command()
def validate(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to the repo root (default: current directory)"
    ),
) -> None:
    """Validate mcp-plugin.toml without generating anything."""
    repo_root = repo_root.resolve()
    try:
        cfg = load_config(repo_root)
    except FileNotFoundError:
        typer.echo(f"Error: No mcp-plugin.toml found in {repo_root}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error: Invalid config: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"Valid: {cfg.name} v{cfg.version}")
    typer.echo(f"  Server: {cfg.server.command} {' '.join(cfg.server.args)}")
    if cfg.cli:
        typer.echo(f"  CLI: {cfg.cli.name} ({cfg.cli.entry_point})")
    typer.echo(f"  Skills: {len(cfg.skills)}")
    typer.echo(f"  Rules: {len(cfg.rules)}")
    typer.echo(f"  Hooks: {len(cfg.hooks)}")

    for skill in cfg.skills:
        src = repo_root / skill.path
        if not src.exists():
            typer.echo(f"  WARNING: Skill source missing: {skill.path}", err=True)
    for rule in cfg.rules:
        src = repo_root / rule.path
        if not src.exists():
            typer.echo(f"  WARNING: Rule source missing: {rule.path}", err=True)


@app.command()
def check(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to the repo root (default: current directory)"
    ),
) -> None:
    """Check if generated files are in sync with mcp-plugin.toml. Exits 1 if stale."""
    from mcp_common.plugin_precommit import check_sync

    repo_root = repo_root.resolve()
    in_sync, stale = check_sync(repo_root)

    if in_sync:
        typer.echo("All generated files are in sync with mcp-plugin.toml")
    else:
        typer.echo("Generated files are OUT OF SYNC with mcp-plugin.toml:", err=True)
        for f in stale:
            typer.echo(f"  {f}", err=True)
        typer.echo("\nRun `mcp-plugin-gen generate .` to fix.", err=True)
        raise typer.Exit(1)


@app.command("registry-entry")
def registry_entry(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to the repo root (default: current directory)"
    ),
) -> None:
    """Generate only .claude-plugin/registry-entry.json from mcp-plugin.toml."""
    repo_root = repo_root.resolve()
    try:
        cfg = load_config(repo_root)
    except FileNotFoundError:
        typer.echo(f"Error: No mcp-plugin.toml found in {repo_root}", err=True)
        raise typer.Exit(1) from None

    files = generate_registry_entry(cfg, repo_root)
    for file_path in files:
        typer.echo(file_path)


@app.command("aggregate-marketplace")
def aggregate_marketplace(
    entries_dir: Path = typer.Argument(  # noqa: B008
        ..., help="Directory containing registry-entry JSON files"
    ),
    output_file: Path = typer.Argument(..., help="Output marketplace file"),  # noqa: B008
) -> None:
    """Aggregate registry-entry files into one deterministic marketplace file."""
    try:
        output = aggregate_marketplace_entries(entries_dir, output_file)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    except Exception as e:
        typer.echo(f"Error: Invalid registry entry: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(str(output))


MARKETPLACE_PLATFORMS = ("cursor", "opencode", "openhands", "claude")


@app.command("build-marketplace")
def build_marketplace(
    repos_dir: Path = typer.Argument(  # noqa: B008
        ..., help="Directory containing cloned MCP repos (each with mcp-plugin.toml)"
    ),
    output_dir: Path = typer.Argument(  # noqa: B008
        ..., help="Output directory for marketplace directories"
    ),
    platform: str | None = typer.Option(
        None,
        "--platform",
        "-p",
        help=f"Build only one platform ({', '.join(MARKETPLACE_PLATFORMS)}). Omit for all.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be built"),
) -> None:
    """Build aggregated marketplace directories from a set of cloned MCP repos."""
    if platform and platform not in MARKETPLACE_PLATFORMS:
        typer.echo(
            f"Error: Unknown platform '{platform}'. "
            f"Choose from: {', '.join(MARKETPLACE_PLATFORMS)}",
            err=True,
        )
        raise typer.Exit(1)

    results = build_marketplace_all(
        repos_dir.resolve(), output_dir.resolve(), dry_run=dry_run, platform=platform
    )
    if not results:
        raise typer.Exit(1)

    total = sum(len(f) for f in results.values())
    typer.echo(f"\nBuilt {total} files across {len(results)} platform(s)")


def _referenced_env_vars(cfg_env: dict[str, str]) -> list[str]:
    refs: list[str] = []
    for value in cfg_env.values():
        m = ENV_REF_RE.match(value.strip())
        if m:
            refs.append(m.group(1))
    # preserve order, de-duplicate
    return list(dict.fromkeys(refs))


@app.command()
def doctor(
    repo_root: Path = typer.Argument(  # noqa: B008
        Path("."), help="Path to repo root containing mcp-plugin.toml"
    ),
    check_op: bool = typer.Option(
        True, "--check-op/--no-check-op", help="Check 1Password CLI availability/session"
    ),
) -> None:
    """Validate runtime secret prerequisites for this MCP plugin."""
    repo_root = repo_root.resolve()
    cfg = load_config(repo_root)

    missing: list[str] = []
    referenced = _referenced_env_vars(cfg.server.env)

    typer.echo(f"Doctor: {cfg.name} v{cfg.version}")
    typer.echo("\nEnvironment references:")
    if not referenced:
        typer.echo("  (none)")
    for var in referenced:
        value = os.getenv(var, "")
        ok = bool(value.strip())
        status = "ok" if ok else "missing"
        typer.echo(f"  {var}: {status}")
        if not ok:
            missing.append(var)

    op_ok = True
    if check_op:
        typer.echo("\n1Password CLI:")
        try:
            ver = subprocess.run(
                ["op", "--version"], capture_output=True, text=True, timeout=5, check=False
            )
            if ver.returncode != 0:
                op_ok = False
            else:
                typer.echo(f"  op: {ver.stdout.strip() or 'ok'}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            op_ok = False
        if not op_ok:
            typer.echo("  op: missing/unavailable")
        else:
            whoami = subprocess.run(
                ["op", "whoami"], capture_output=True, text=True, timeout=5, check=False
            )
            if whoami.returncode != 0:
                typer.echo("  session: not authenticated (run `op signin` or use service account)")
                op_ok = False
            else:
                typer.echo("  session: authenticated")

    if missing:
        typer.echo("\nMissing required env vars for MCP runtime.", err=True)
    if check_op and not op_ok:
        typer.echo("1Password CLI/session not ready.", err=True)
    if missing or (check_op and not op_ok):
        raise typer.Exit(1)
    typer.echo("\nAll checks passed.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
