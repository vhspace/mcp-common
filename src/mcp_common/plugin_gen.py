"""
mcp-plugin-gen: Generate platform-specific plugin configs from mcp-plugin.toml.

Reads a universal config and produces:
  .cursor-plugin/     — Cursor IDE plugin
  .claude-plugin/     — Claude Code plugin
  .mcp.json           — MCP server config (both formats)
  hooks/              — Root hooks (Claude Code)
  skills/             — Root skills (cross-platform)
  AGENTS.md           — Generic agent instructions
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from mcp_common.plugin_schema import PluginConfig


PLATFORMS = ["cursor", "claude", "opencode", "agents-md"]


def load_config(repo_root: Path) -> PluginConfig:
    """Load mcp-plugin.toml from a repo root."""
    config_path = repo_root / "mcp-plugin.toml"
    if not config_path.exists():
        raise FileNotFoundError(f"No mcp-plugin.toml found at {config_path}")
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    return PluginConfig(**raw)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _write_text(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def _base_plugin_json(cfg: PluginConfig) -> dict[str, Any]:
    return {
        "name": cfg.name,
        "description": cfg.description,
        "version": cfg.version,
        "author": cfg.author.model_dump(exclude_none=True),
        "repository": cfg.repository,
        "license": cfg.license,
        "keywords": cfg.keywords,
    }


def generate_cursor(cfg: PluginConfig, repo_root: Path) -> list[str]:
    """Generate .cursor-plugin/ directory."""
    out = repo_root / ".cursor-plugin"
    files: list[str] = []

    plugin = _base_plugin_json(cfg)
    plugin["mcpServers"] = {
        cfg.name: {
            "command": cfg.server.command,
            "args": cfg.server.args,
            "env": cfg.server.env,
        }
    }
    if cfg.skills:
        plugin["skills"] = "./skills/"
    if cfg.rules:
        plugin["rules"] = "./rules/"
    if cfg.hooks:
        plugin["hooks"] = "./hooks/hooks.json"

    _write_json(out / "plugin.json", plugin)
    files.append(".cursor-plugin/plugin.json")

    for skill in cfg.skills:
        src = repo_root / skill.path
        dst = out / "skills" / skill.name / "SKILL.md"
        if _copy_if_exists(src, dst):
            files.append(f".cursor-plugin/skills/{skill.name}/SKILL.md")

    for rule in cfg.rules:
        src = repo_root / rule.path
        dst = out / "rules" / Path(rule.path).name
        if _copy_if_exists(src, dst):
            files.append(f".cursor-plugin/rules/{Path(rule.path).name}")

    if cfg.hooks:
        hooks_json = _build_hooks_json(cfg)
        _write_json(out / "hooks" / "hooks.json", hooks_json)
        files.append(".cursor-plugin/hooks/hooks.json")

        if cfg.cli:
            script = _build_setup_cli_script(cfg)
            _write_text(out / "hooks" / "setup-cli", script, executable=True)
            files.append(".cursor-plugin/hooks/setup-cli")

    return files


def generate_claude(cfg: PluginConfig, repo_root: Path) -> list[str]:
    """Generate .claude-plugin/ directory + root hooks/ and skills/."""
    out = repo_root / ".claude-plugin"
    files: list[str] = []

    plugin = _base_plugin_json(cfg)
    if cfg.hooks:
        plugin["hooks"] = "../hooks/hooks.json"
    plugin["mcpServers"] = {
        cfg.name: {
            "command": cfg.server.command,
            "args": cfg.server.args,
            "env": cfg.server.env,
        }
    }

    _write_json(out / "plugin.json", plugin)
    files.append(".claude-plugin/plugin.json")

    marketplace = {
        "name": f"{cfg.name}-marketplace",
        "description": cfg.description,
        "owner": cfg.author.model_dump(exclude_none=True),
        "plugins": [
            {
                "name": cfg.name,
                "description": cfg.description,
                "version": cfg.version,
                "source": "./",
                "author": cfg.author.model_dump(exclude_none=True),
            }
        ],
    }
    _write_json(out / "marketplace.json", marketplace)
    files.append(".claude-plugin/marketplace.json")

    for skill in cfg.skills:
        src = repo_root / skill.path
        dst = repo_root / "skills" / skill.name / "SKILL.md"
        if _copy_if_exists(src, dst):
            files.append(f"skills/{skill.name}/SKILL.md")

    if cfg.hooks:
        hooks_json = _build_hooks_json(cfg)
        _write_json(repo_root / "hooks" / "hooks.json", hooks_json)
        files.append("hooks/hooks.json")

        if cfg.cli:
            script = _build_setup_cli_script(cfg)
            _write_text(repo_root / "hooks" / "setup-cli", script, executable=True)
            files.append("hooks/setup-cli")

    return files


def generate_mcp_json(cfg: PluginConfig, repo_root: Path) -> list[str]:
    """Generate .mcp.json (Claude Code flat format, also usable by Cursor via plugin.json)."""
    mcp_config = {
        cfg.name: {
            "command": cfg.server.command,
            "args": cfg.server.args,
            "env": cfg.server.env,
        }
    }
    _write_json(repo_root / ".mcp.json", mcp_config)
    return [".mcp.json"]


def generate_agents_md(cfg: PluginConfig, repo_root: Path) -> list[str]:
    """Generate/update AGENTS.md with plugin info for generic clients."""
    lines = [
        f"# {cfg.name}",
        "",
        cfg.description,
        "",
    ]

    if cfg.cli:
        lines.extend([
            f"## CLI: `{cfg.cli.name}`",
            "",
            f"Run `{cfg.cli.name} --help` for all commands.",
            f"Install: `uvx --from {cfg.name} {cfg.cli.name}`",
            "",
        ])

    lines.extend([
        "## MCP Server",
        "",
        f"```bash",
        f"{cfg.server.command} {' '.join(cfg.server.args)}",
        f"```",
        "",
    ])

    if cfg.server.env:
        lines.extend(["### Required env vars", ""])
        for k, v in cfg.server.env.items():
            lines.append(f"- `{k}`: {v if v.startswith('$') else '(set in .env)'}")
        lines.append("")

    _write_text(repo_root / "AGENTS.md", "\n".join(lines))
    return ["AGENTS.md"]


def _build_hooks_json(cfg: PluginConfig) -> dict[str, Any]:
    hooks_by_event: dict[str, list[dict[str, Any]]] = {}
    for hook in cfg.hooks:
        entry = {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": f"'${{CLAUDE_PLUGIN_ROOT}}/hooks/{Path(hook.script).name}'",
                    "async": hook.async_,
                }
            ],
        }
        hooks_by_event.setdefault(hook.event, []).append(entry)
    return {"hooks": hooks_by_event}


def _build_setup_cli_script(cfg: PluginConfig) -> str:
    if not cfg.cli:
        return ""

    env_discovery = "\n".join(
        f'  if [[ -n "$candidate" && -f "$candidate" ]]; then\n    ENV_FILE="$candidate"\n    break\n  fi'
        for _ in cfg.env_file_discovery
    )

    candidates = " ".join(f'"{p}"' for p in cfg.env_file_discovery)

    repo_url = cfg.repository.replace("https://github.com/", "")
    return f"""#!/usr/bin/env bash
set -euo pipefail

CLI_NAME="{cfg.cli.name}"
REPO="{repo_url}"
VERSION="v{cfg.version}"
TARGET="$HOME/.local/bin/$CLI_NAME"

if [[ -f "$TARGET" ]]; then
  exit 0
fi

mkdir -p "$HOME/.local/bin"

ENV_FILE=""
for candidate in {candidates}; do
  candidate="${{candidate//\\~/$HOME}}"
  [[ -z "$candidate" || "$candidate" == "/.env" ]] && continue
  if [[ -f "$candidate" ]]; then
    ENV_FILE="$candidate"
    break
  fi
done

cat > "$TARGET" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
${{ENV_FILE:+set -a; source "$ENV_FILE"; set +a}}
exec uvx --from "git+https://github.com/$REPO@$VERSION" "$CLI_NAME" "\\$@"
WRAPPER

chmod +x "$TARGET"

echo '{{"additional_context": "'"$CLI_NAME"' installed to ~/.local/bin/'"$CLI_NAME"'", "hookSpecificOutput": {{"hookEventName": "SessionStart", "additionalContext": ""}}}}'
"""


def generate_all(cfg: PluginConfig, repo_root: Path) -> dict[str, list[str]]:
    """Generate all platform configs. Returns dict of platform -> files created."""
    return {
        "cursor": generate_cursor(cfg, repo_root),
        "claude": generate_claude(cfg, repo_root),
        "mcp.json": generate_mcp_json(cfg, repo_root),
        "agents-md": generate_agents_md(cfg, repo_root),
    }
