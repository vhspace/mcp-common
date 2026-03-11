"""
CLI entry point for mcp-plugin-gen.

Usage:
    mcp-plugin-gen                  # generate all platforms
    mcp-plugin-gen --platform cursor
    mcp-plugin-gen --platform claude
    mcp-plugin-gen --dry-run        # show what would be generated
    mcp-plugin-gen --init           # create a starter mcp-plugin.toml
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from mcp_common.plugin_gen import (
    generate_agents_md,
    generate_all,
    generate_claude,
    generate_cursor,
    generate_mcp_json,
    generate_opencode,
    generate_openhands,
    load_config,
)

app = typer.Typer(
    name="mcp-plugin-gen",
    help="Generate platform-specific plugin configs from mcp-plugin.toml.",
    no_args_is_help=False,
)

GENERATORS = {
    "cursor": generate_cursor,
    "claude": generate_claude,
    "mcp-json": generate_mcp_json,
    "opencode": generate_opencode,
    "openhands": generate_openhands,
    "agents-md": generate_agents_md,
}

STARTER_TOML = '''# MCP Plugin Config — single source of truth for all platforms.
# Run `mcp-plugin-gen` to produce Cursor, Claude Code, OpenCode, etc. configs.

name = "{name}"
description = "{description}"
version = "{version}"
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
'''


@app.command()
def generate(
    repo_root: Path = typer.Argument(
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
        raise typer.Exit(1)

    if platform:
        if platform not in GENERATORS:
            typer.echo(f"Error: Unknown platform '{platform}'. Choose from: {', '.join(GENERATORS)}", err=True)
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
    repo_root: Path = typer.Argument(
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
        version="0.1.0",
        cli_name=cli_name,
        pkg=f"{pkg_name}_mcp",
    )

    with open(target, "w") as f:
        f.write(content)

    typer.echo(f"Created {target}")

    precommit_path = repo_root.resolve() / ".pre-commit-config.yaml"
    hook_block = """  - repo: https://github.com/vhspace/mcp-common
    rev: v0.3.0
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

    typer.echo("Edit mcp-plugin.toml, then run `mcp-plugin-gen generate .`")


@app.command()
def validate(
    repo_root: Path = typer.Argument(
        Path("."), help="Path to the repo root (default: current directory)"
    ),
) -> None:
    """Validate mcp-plugin.toml without generating anything."""
    repo_root = repo_root.resolve()
    try:
        cfg = load_config(repo_root)
    except FileNotFoundError:
        typer.echo(f"Error: No mcp-plugin.toml found in {repo_root}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: Invalid config: {e}", err=True)
        raise typer.Exit(1)

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
    repo_root: Path = typer.Argument(
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
