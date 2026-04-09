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
import re
import shutil
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mcp_common.plugin_schema import PluginConfig

PLATFORMS = ["cursor", "claude", "opencode", "openhands", "agents-md"]


class RegistryMCPServer(BaseModel):
    """MCP server launch settings embedded in registry entries."""

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class RegistryEntry(BaseModel):
    """Claude private marketplace registry-entry artifact."""

    schema_version: int = Field(1, alias="schemaVersion")
    name: str
    description: str = ""
    version: str
    repository: str
    license: str | None = None
    author: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    mcp_server: RegistryMCPServer | None = Field(None, alias="mcpServer")
    plugin_path: str = Field(".claude-plugin/plugin.json", alias="pluginPath")

    model_config = {"populate_by_name": True}


@dataclass(frozen=True)
class LoadedPluginConfig:
    """Plugin metadata from mcp-plugin.toml plus version from pyproject.toml."""

    config: PluginConfig
    version: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.config, name)


def _load_pyproject_version(repo_root: Path) -> str:
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"No pyproject.toml found at {pyproject_path}")
    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)
    version = pyproject.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("pyproject.toml must define [project].version")
    return version


def load_config(repo_root: Path) -> LoadedPluginConfig:
    """Load mcp-plugin.toml from a repo root and resolve version from pyproject.toml."""
    config_path = repo_root / "mcp-plugin.toml"
    if not config_path.exists():
        raise FileNotFoundError(f"No mcp-plugin.toml found at {config_path}")
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    if "version" in raw:
        raise ValueError(
            "mcp-plugin.toml must not define `version`; use pyproject.toml [project].version"
        )
    config = PluginConfig(**raw)
    return LoadedPluginConfig(config=config, version=_load_pyproject_version(repo_root))


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
        if src.resolve() == dst.resolve():
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def _base_plugin_json(cfg: LoadedPluginConfig) -> dict[str, Any]:
    return {
        "name": cfg.name,
        "description": cfg.description,
        "version": cfg.version,
        "author": cfg.author.model_dump(exclude_none=True),
        "repository": cfg.repository,
        "license": cfg.license,
        "keywords": cfg.keywords,
    }


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(dict.fromkeys(values), key=str.lower)


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    pieces = re.split(r"[.\-+_]", version.strip().lower())
    key: list[tuple[int, int | str]] = []
    for piece in pieces:
        if piece.isdigit():
            key.append((0, int(piece)))
        else:
            key.append((1, piece))
    return tuple(key)


def _build_registry_entry(cfg: LoadedPluginConfig) -> RegistryEntry:
    env_sorted = {k: cfg.server.env[k] for k in sorted(cfg.server.env)}
    categories: list[str] = []
    tags: list[str] = []
    if cfg.marketplace:
        categories = _sorted_unique(cfg.marketplace.categories)
        tags = _sorted_unique(cfg.marketplace.tags)

    return RegistryEntry(
        name=cfg.name,
        description=cfg.description,
        version=cfg.version,
        repository=cfg.repository,
        license=cfg.license,
        author=cfg.author.model_dump(exclude_none=True),
        keywords=_sorted_unique(cfg.keywords),
        categories=categories,
        tags=tags,
        mcp_server=RegistryMCPServer(
            command=cfg.server.command,
            args=list(cfg.server.args),
            env=env_sorted,
        ),
    )


def generate_registry_entry(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
    """Generate Claude private marketplace registry entry artifact."""
    out = repo_root / ".claude-plugin" / "registry-entry.json"
    entry = _build_registry_entry(cfg).model_dump(by_alias=True, exclude_none=True)
    _write_json(out, entry)
    return [".claude-plugin/registry-entry.json"]


def aggregate_marketplace_entries(entries_dir: Path, output_file: Path) -> Path:
    """
    Aggregate registry-entry JSON files into one deterministic marketplace file.

    De-duplicates by plugin name and keeps the highest version.
    """
    entries_dir = entries_dir.resolve()
    if not entries_dir.exists() or not entries_dir.is_dir():
        raise FileNotFoundError(f"Entries directory does not exist: {entries_dir}")

    candidates = sorted(entries_dir.rglob("*.json"))
    parsed: list[RegistryEntry] = []
    for candidate in candidates:
        with open(candidate) as f:
            payload = json.load(f)
        parsed.append(RegistryEntry.model_validate(payload))

    if not parsed:
        raise ValueError(f"No registry-entry JSON files found under {entries_dir}")

    best_by_name: dict[str, RegistryEntry] = {}
    for entry in sorted(
        parsed, key=lambda item: (item.name.lower(), _version_sort_key(item.version))
    ):
        name_key = entry.name.lower()
        existing = best_by_name.get(name_key)
        if existing is None or _version_sort_key(entry.version) > _version_sort_key(
            existing.version
        ):
            best_by_name[name_key] = entry

    deduped = sorted(best_by_name.values(), key=lambda item: item.name.lower())
    marketplace = {
        "schemaVersion": 1,
        "entries": [item.model_dump(by_alias=True, exclude_none=True) for item in deduped],
    }
    _write_json(output_file, marketplace)
    return output_file


def generate_cursor(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
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


def generate_claude(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
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
    files.extend(generate_registry_entry(cfg, repo_root))

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


def generate_mcp_json(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
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


def generate_opencode(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
    """Generate opencode.json and .opencode/skills/ directory."""
    files: list[str] = []

    opencode_config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            cfg.name: {
                "type": "local",
                "command": [cfg.server.command, *cfg.server.args],
                "environment": cfg.server.env,
                "enabled": True,
            }
        },
    }
    _write_json(repo_root / "opencode.json", opencode_config)
    files.append("opencode.json")

    for skill in cfg.skills:
        src = repo_root / skill.path
        dst = repo_root / ".opencode" / "skills" / skill.name / "SKILL.md"
        if _copy_if_exists(src, dst):
            files.append(f".opencode/skills/{skill.name}/SKILL.md")

    return files


def generate_openhands(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
    """Generate .openhands/mcp.json with Claude Code-style server config."""
    mcp_config = {
        "mcpServers": {
            cfg.name: {
                "command": cfg.server.command,
                "args": cfg.server.args,
                "env": cfg.server.env,
            }
        }
    }
    _write_json(repo_root / ".openhands" / "mcp.json", mcp_config)
    return [".openhands/mcp.json"]


def generate_agents_md(cfg: LoadedPluginConfig, repo_root: Path) -> list[str]:
    """Generate/update AGENTS.md with plugin info for generic clients."""
    lines = [
        f"# {cfg.name}",
        "",
        cfg.description,
        "",
    ]

    if cfg.cli:
        lines.extend(
            [
                f"## CLI: `{cfg.cli.name}`",
                "",
                f"Run `{cfg.cli.name} --help` for all commands.",
                f"Install: `uvx --from {cfg.name} {cfg.cli.name}`",
                "",
            ]
        )

    lines.extend(
        [
            "## MCP Server",
            "",
            "```bash",
            f"{cfg.server.command} {' '.join(cfg.server.args)}",
            "```",
            "",
        ]
    )

    if cfg.server.env:
        lines.extend(["### Required env vars", ""])
        for k, v in cfg.server.env.items():
            lines.append(f"- `{k}`: {v if v.startswith('$') else '(set in .env)'}")
        lines.append("")

    _write_text(repo_root / "AGENTS.md", "\n".join(lines))
    return ["AGENTS.md"]


def _build_hooks_json(cfg: LoadedPluginConfig) -> dict[str, Any]:
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


def _build_setup_cli_script(cfg: LoadedPluginConfig) -> str:
    if not cfg.cli:
        return ""

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

cat > "$TARGET" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
exec uvx --from "git+https://github.com/$REPO@$VERSION" "$CLI_NAME" "\\$@"
WRAPPER

chmod +x "$TARGET"

echo '{{"additional_context": "'"$CLI_NAME"' installed to ~/.local/bin/'"$CLI_NAME"'", "hookSpecificOutput": {{"hookEventName": "SessionStart", "additionalContext": ""}}}}'
"""


def generate_all(cfg: LoadedPluginConfig, repo_root: Path) -> dict[str, list[str]]:
    """Generate all platform configs. Returns dict of platform -> files created."""
    return {
        "cursor": generate_cursor(cfg, repo_root),
        "claude": generate_claude(cfg, repo_root),
        "mcp.json": generate_mcp_json(cfg, repo_root),
        "opencode": generate_opencode(cfg, repo_root),
        "openhands": generate_openhands(cfg, repo_root),
        "agents-md": generate_agents_md(cfg, repo_root),
    }
