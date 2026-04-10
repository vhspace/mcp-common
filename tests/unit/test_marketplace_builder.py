"""Tests for marketplace_builder — multi-platform aggregated marketplace generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_common.marketplace_builder import (
    build_all,
    build_claude_marketplace,
    build_opencode_marketplace,
    build_openhands_marketplace,
    discover_plugins,
)


def _write_plugin_repo(
    root: Path,
    name: str = "example-mcp",
    version: str = "1.2.3",
    *,
    with_skill: bool = False,
    with_env: bool = False,
) -> None:
    """Create a minimal mcp-plugin.toml + pyproject.toml in *root*."""
    root.mkdir(parents=True, exist_ok=True)
    env_block = ""
    if with_env:
        env_block = '\n[server.env]\nMY_TOKEN = "${MY_TOKEN}"\nMY_URL = "${MY_URL}"\n'

    skill_block = ""
    if with_skill:
        skill_block = (
            '\n[[skills]]\nname = "example-ops"\n'
            'description = "Use when ..."\n'
            'path = "skills/example-ops/SKILL.md"\n'
        )
        skill_dir = root / "skills" / "example-ops"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Example Ops\nUse this skill.\n")

    (root / "mcp-plugin.toml").write_text(
        f'name = "{name}"\n'
        f'description = "{name} server"\n'
        f'repository = "https://github.com/vhspace/{name}"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        f'args = ["--from", "{name}", "{name}"]\n'
        f"{env_block}"
        f"{skill_block}"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f'description = "{name} server"\n'
        'requires-python = ">=3.12"\n'
    )


def _make_repos_dir(tmp_path: Path, count: int = 2) -> Path:
    """Create a repos dir with *count* plugin repos plus one non-plugin dir."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    names = [f"plugin-{chr(97 + i)}-mcp" for i in range(count)]
    for i, name in enumerate(names):
        _write_plugin_repo(
            repos_dir / name,
            name=name,
            version=f"1.{i}.0",
            with_env=(i % 2 == 0),
            with_skill=(i == 0),
        )
    # Non-plugin directory (no mcp-plugin.toml)
    (repos_dir / "not-a-plugin").mkdir()
    (repos_dir / "not-a-plugin" / "README.md").write_text("nothing here\n")
    return repos_dir


class TestDiscoverPlugins:
    def test_finds_repos_with_config(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=3)
        plugins = discover_plugins(repos_dir)
        assert len(plugins) == 3
        names = [cfg.name for _, cfg in plugins]
        assert names == ["plugin-a-mcp", "plugin-b-mcp", "plugin-c-mcp"]

    def test_skips_repos_without_config(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        assert len(plugins) == 1
        assert plugins[0][1].name == "plugin-a-mcp"

    def test_raises_for_missing_dir(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_plugins(tmp_path / "nonexistent")

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        repos_dir = tmp_path / "empty"
        repos_dir.mkdir()
        assert discover_plugins(repos_dir) == []


class TestBuildOpencodeMarketplace:
    def test_generates_aggregated_opencode_json(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=2)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "opencode-out"

        files = build_opencode_marketplace(plugins, output_dir)

        assert "opencode.json" in files
        data = json.loads((output_dir / "opencode.json").read_text())
        assert "$schema" in data
        assert "mcp" in data
        assert len(data["mcp"]) == 2

    def test_uses_git_https_pinned_args(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "opencode-out"

        build_opencode_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "opencode.json").read_text())
        server = data["mcp"]["plugin-a-mcp"]
        assert server["type"] == "local"
        assert server["enabled"] is True
        assert any(
            "git+https://github.com/vhspace/plugin-a-mcp@v1.0.0" in str(c)
            for c in server["command"]
        )

    def test_copies_skills(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "opencode-out"

        files = build_opencode_marketplace(plugins, output_dir)

        skill_files = [f for f in files if "SKILL.md" in f]
        assert len(skill_files) >= 1

    def test_includes_env_vars(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "opencode-out"

        build_opencode_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "opencode.json").read_text())
        env = data["mcp"]["plugin-a-mcp"]["environment"]
        assert "MY_TOKEN" in env


class TestBuildOpenhandsMarketplace:
    def test_generates_aggregated_mcp_json(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=2)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "openhands-out"

        files = build_openhands_marketplace(plugins, output_dir)

        assert "mcp.json" in files
        data = json.loads((output_dir / "mcp.json").read_text())
        assert "mcpServers" in data
        assert len(data["mcpServers"]) == 2

    def test_uses_git_https_pinned_args(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "openhands-out"

        build_openhands_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "mcp.json").read_text())
        server = data["mcpServers"]["plugin-a-mcp"]
        assert server["command"] == "uvx"
        assert "git+https://github.com/vhspace/plugin-a-mcp@v1.0.0" in server["args"]

    def test_includes_env_vars(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "openhands-out"

        build_openhands_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "mcp.json").read_text())
        env = data["mcpServers"]["plugin-a-mcp"]["env"]
        assert "MY_TOKEN" in env


class TestBuildClaudeMarketplace:
    def test_generates_marketplace_json(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=2)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "claude-out"

        files = build_claude_marketplace(plugins, output_dir)

        assert "marketplace.json" in files
        data = json.loads((output_dir / "marketplace.json").read_text())
        assert data["schemaVersion"] == 1
        assert len(data["entries"]) == 2

    def test_entries_sorted_by_name(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=3)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "claude-out"

        build_claude_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "marketplace.json").read_text())
        names = [e["name"] for e in data["entries"]]
        assert names == sorted(names, key=str.lower)

    def test_entries_have_registry_fields(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        plugins = discover_plugins(repos_dir)
        output_dir = tmp_path / "claude-out"

        build_claude_marketplace(plugins, output_dir)

        data = json.loads((output_dir / "marketplace.json").read_text())
        entry = data["entries"][0]
        assert "name" in entry
        assert "version" in entry
        assert "mcpServer" in entry
        assert "args" in entry["mcpServer"]


class TestBuildAll:
    def test_produces_all_four_platforms(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=2)
        output_dir = tmp_path / "output"

        results = build_all(repos_dir, output_dir)

        assert set(results.keys()) == {"cursor", "opencode", "openhands", "claude"}
        for plat, files in results.items():
            assert len(files) > 0, f"{plat} produced no files"

    def test_single_platform_filter(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        output_dir = tmp_path / "output"

        results = build_all(repos_dir, output_dir, platform="opencode")

        assert list(results.keys()) == ["opencode"]
        assert (output_dir / "opencode-marketplace" / "opencode.json").exists()
        assert not (output_dir / "claude-marketplace").exists()

    def test_dry_run_produces_no_files(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        output_dir = tmp_path / "output"

        results = build_all(repos_dir, output_dir, dry_run=True)

        assert all(len(files) == 0 for files in results.values())
        assert not (output_dir / "opencode-marketplace").exists()

    def test_empty_repos_dir_returns_empty(self, tmp_path: Path) -> None:
        repos_dir = tmp_path / "empty"
        repos_dir.mkdir()
        output_dir = tmp_path / "output"

        results = build_all(repos_dir, output_dir)

        assert results == {}

    def test_marketplace_dirs_created(self, tmp_path: Path) -> None:
        repos_dir = _make_repos_dir(tmp_path, count=1)
        output_dir = tmp_path / "output"

        build_all(repos_dir, output_dir)

        assert (output_dir / "opencode-marketplace" / "opencode.json").exists()
        assert (output_dir / "openhands-marketplace" / "mcp.json").exists()
        assert (output_dir / "claude-marketplace" / "marketplace.json").exists()
        assert (output_dir / "cursor-marketplace").is_dir()
