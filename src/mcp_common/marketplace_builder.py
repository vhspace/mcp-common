"""
Multi-platform marketplace builder.

Discovers cloned MCP repos, loads their mcp-plugin.toml configs,
and generates aggregated marketplace artifacts for each supported platform:
  - cursor-marketplace/      (Cursor IDE)
  - opencode-marketplace/    (OpenCode)
  - openhands-marketplace/   (OpenHands)
  - claude-marketplace/      (Claude private marketplace)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from mcp_common.plugin_gen import (
    LoadedPluginConfig,
    _build_registry_entry,
    _resolve_server_args,
    _write_json,
    generate_cursor,
    load_config,
)


def discover_plugins(repos_dir: Path) -> list[tuple[Path, LoadedPluginConfig]]:
    """Walk *repos_dir* and return (repo_path, config) for each valid MCP plugin repo."""
    repos_dir = repos_dir.resolve()
    if not repos_dir.is_dir():
        raise FileNotFoundError(f"Repos directory does not exist: {repos_dir}")

    plugins: list[tuple[Path, LoadedPluginConfig]] = []
    for child in sorted(repos_dir.iterdir()):
        if not child.is_dir():
            continue
        config_path = child / "mcp-plugin.toml"
        pyproject_path = child / "pyproject.toml"
        if not config_path.exists() or not pyproject_path.exists():
            continue
        try:
            cfg = load_config(child)
        except (FileNotFoundError, ValueError):
            continue
        plugins.append((child, cfg))
    return plugins


def _copy_skills_to_dir(cfg: LoadedPluginConfig, repo_root: Path, dest_dir: Path) -> list[str]:
    """Copy skill files into dest_dir/<plugin-name>/<skill-name>/SKILL.md."""
    files: list[str] = []
    for skill in cfg.skills:
        src = repo_root / skill.path
        if not src.exists():
            continue
        dst = dest_dir / cfg.name / skill.name / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append(str(dst.relative_to(dest_dir.parent)))
    return files


def build_opencode_marketplace(
    plugins: list[tuple[Path, LoadedPluginConfig]], output_dir: Path
) -> list[str]:
    """Generate aggregated opencode.json with all MCP servers using git+https pinned args."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    mcp_servers: dict[str, Any] = {}
    for _repo_root, cfg in plugins:
        resolved_args = _resolve_server_args(cfg)
        mcp_servers[cfg.name] = {
            "type": "local",
            "command": [cfg.server.command, *resolved_args],
            "environment": cfg.server.env,
            "enabled": True,
        }

    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_servers,
    }
    out_path = output_dir / "opencode.json"
    _write_json(out_path, opencode_config)
    files.append("opencode.json")

    skills_dir = output_dir / "skills"
    for repo_root, cfg in plugins:
        skill_files = _copy_skills_to_dir(cfg, repo_root, skills_dir)
        files.extend(skill_files)

    return files


def build_openhands_marketplace(
    plugins: list[tuple[Path, LoadedPluginConfig]], output_dir: Path
) -> list[str]:
    """Generate aggregated mcp.json with mcpServers key using git+https pinned args."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    mcp_servers: dict[str, Any] = {}
    for _repo_root, cfg in plugins:
        resolved_args = _resolve_server_args(cfg)
        mcp_servers[cfg.name] = {
            "command": cfg.server.command,
            "args": resolved_args,
            "env": cfg.server.env,
        }

    openhands_config = {"mcpServers": mcp_servers}
    out_path = output_dir / "mcp.json"
    _write_json(out_path, openhands_config)
    files.append("mcp.json")

    return files


def build_claude_marketplace(
    plugins: list[tuple[Path, LoadedPluginConfig]], output_dir: Path
) -> list[str]:
    """Generate marketplace.json with schemaVersion 1 and entries array from registry entries."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    entries = []
    for _repo_root, cfg in plugins:
        entry = _build_registry_entry(cfg)
        entries.append(entry.model_dump(by_alias=True, exclude_none=True))

    entries.sort(key=lambda e: e.get("name", "").lower())

    marketplace = {
        "schemaVersion": 1,
        "entries": entries,
    }
    out_path = output_dir / "marketplace.json"
    _write_json(out_path, marketplace)
    files.append("marketplace.json")

    return files


def build_cursor_marketplace(
    plugins: list[tuple[Path, LoadedPluginConfig]], output_dir: Path
) -> list[str]:
    """Generate per-plugin Cursor directories under output_dir/<plugin-name>/."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    for repo_root, cfg in plugins:
        plugin_dir = output_dir / cfg.name
        plugin_dir.mkdir(parents=True, exist_ok=True)

        shutil.copytree(repo_root, plugin_dir, dirs_exist_ok=True)
        generated = generate_cursor(cfg, plugin_dir)
        files.extend(f"{cfg.name}/{f}" for f in generated)

    return files


def build_all(
    repos_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    platform: str | None = None,
) -> dict[str, list[str]]:
    """Orchestrate marketplace builds for all (or a single) platform.

    Returns mapping of platform name -> list of relative file paths created.
    """
    output_dir = output_dir.resolve()
    plugins = discover_plugins(repos_dir)

    if not plugins:
        print(f"No plugins found in {repos_dir}", file=sys.stderr)
        return {}

    if dry_run:
        print(f"Discovered {len(plugins)} plugins:")
        for repo_root, cfg in plugins:
            print(f"  {cfg.name} v{cfg.version}  ({repo_root.name})")
        return {p: [] for p in ["cursor", "opencode", "openhands", "claude"]}

    builders: dict[str, Any] = {
        "cursor": (build_cursor_marketplace, output_dir / "cursor-marketplace"),
        "opencode": (build_opencode_marketplace, output_dir / "opencode-marketplace"),
        "openhands": (build_openhands_marketplace, output_dir / "openhands-marketplace"),
        "claude": (build_claude_marketplace, output_dir / "claude-marketplace"),
    }

    if platform:
        if platform not in builders:
            print(
                f"Unknown platform '{platform}'. Choose from: {', '.join(builders)}",
                file=sys.stderr,
            )
            return {}
        builders = {platform: builders[platform]}

    results: dict[str, list[str]] = {}
    for plat, (builder_fn, plat_dir) in builders.items():
        created = builder_fn(plugins, plat_dir)
        results[plat] = created
        print(f"  {plat}: {len(created)} files -> {plat_dir}")

    return results


def main() -> None:
    """CLI entry point for standalone marketplace building."""
    parser = argparse.ArgumentParser(
        description="Build aggregated marketplace directories from cloned MCP repos."
    )
    parser.add_argument("--repos-dir", type=Path, required=True, help="Directory of cloned repos")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory for marketplaces"
    )
    parser.add_argument(
        "--platform",
        choices=["cursor", "opencode", "openhands", "claude"],
        default=None,
        help="Build only one platform (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be built")
    args = parser.parse_args()

    results = build_all(
        args.repos_dir, args.output_dir, dry_run=args.dry_run, platform=args.platform
    )
    if not results:
        sys.exit(1)

    total = sum(len(f) for f in results.values())
    print(f"\nTotal: {total} files across {len(results)} platforms")


if __name__ == "__main__":
    main()
