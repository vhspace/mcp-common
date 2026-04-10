"""Tests for mcp-plugin-gen config loading and version sourcing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_common.plugin_gen import (
    _resolve_server_args,
    aggregate_marketplace_entries,
    build_cursor_marketplace,
    generate_claude,
    generate_cursor,
    load_config,
)


def _write_plugin_toml(path: Path, *, include_version: bool = False) -> None:
    version_line = 'version = "9.9.9"\n' if include_version else ""
    path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        f"{version_line}"
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n'
    )


def _write_pyproject(path: Path, *, include_version: bool = True) -> None:
    version_line = 'version = "1.2.3"\n' if include_version else ""
    path.write_text(
        "[project]\n"
        'name = "example-mcp"\n'
        f"{version_line}"
        'description = "Example MCP server"\n'
        'requires-python = ">=3.12"\n'
    )


def test_load_config_uses_pyproject_version(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=False)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)

    cfg = load_config(tmp_path)

    assert cfg.version == "1.2.3"
    assert cfg.name == "example-mcp"


def test_load_config_rejects_version_in_mcp_plugin_toml(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=True)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)

    with pytest.raises(ValueError, match="must not define `version`"):
        load_config(tmp_path)


def test_load_config_requires_project_version(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=False)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=False)

    with pytest.raises(ValueError, match=r"\[project\]\.version"):
        load_config(tmp_path)


def test_generate_cursor_uses_pyproject_version(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=False)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    generate_cursor(cfg, tmp_path)

    plugin_json = (tmp_path / ".cursor-plugin" / "plugin.json").read_text()
    assert '"version": "1.2.3"' in plugin_json


def test_generate_claude_allows_in_place_skill_paths(tmp_path: Path) -> None:
    plugin_path = tmp_path / "mcp-plugin.toml"
    plugin_path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n\n'
        "[[skills]]\n"
        'name = "example-usage"\n'
        'description = "Use when ..."\n'
        'path = "skills/example-usage/SKILL.md"\n'
    )
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    skill_file = tmp_path / "skills" / "example-usage" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("# Example Skill\n")

    cfg = load_config(tmp_path)
    generate_claude(cfg, tmp_path)

    assert (tmp_path / ".claude-plugin" / "plugin.json").exists()


def test_generate_claude_plugin_manifest_omits_hooks_field(tmp_path: Path) -> None:
    plugin_path = tmp_path / "mcp-plugin.toml"
    plugin_path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n\n'
        "[[hooks]]\n"
        'event = "SessionStart"\n'
        'script = "hooks/setup-cli"\n'
        "async = true\n"
    )
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    generate_claude(cfg, tmp_path)

    plugin = json.loads((tmp_path / ".claude-plugin" / "plugin.json").read_text())
    assert "hooks" not in plugin


def test_generate_cursor_setup_cli_does_not_source_env_file(tmp_path: Path) -> None:
    plugin_path = tmp_path / "mcp-plugin.toml"
    plugin_path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n\n'
        "[cli]\n"
        'name = "example-cli"\n'
        'entry_point = "example_mcp.cli:main"\n\n'
        "[[hooks]]\n"
        'event = "SessionStart"\n'
        'script = "hooks/setup-cli"\n'
        "async = true\n"
    )
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    generate_cursor(cfg, tmp_path)

    setup_script = (tmp_path / ".cursor-plugin" / "hooks" / "setup-cli").read_text()
    assert 'source "$ENV_FILE"' not in setup_script


def test_generate_claude_writes_registry_entry_with_deterministic_fields(tmp_path: Path) -> None:
    plugin_path = tmp_path / "mcp-plugin.toml"
    plugin_path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["zeta", "alpha", "zeta"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n\n'
        "[server.env]\n"
        'Z_VAR = "${Z_VAR}"\n'
        'A_VAR = "${A_VAR}"\n'
    )
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    generated = generate_claude(cfg, tmp_path)
    assert ".claude-plugin/registry-entry.json" in generated

    entry = json.loads((tmp_path / ".claude-plugin" / "registry-entry.json").read_text())
    assert entry["keywords"] == ["alpha", "zeta"]
    assert list(entry["mcpServer"]["env"].keys()) == ["A_VAR", "Z_VAR"]


def test_aggregate_marketplace_entries_sorts_and_dedupes(tmp_path: Path) -> None:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir(parents=True)

    (entries_dir / "alpha-old.json").write_text(
        json.dumps({"name": "alpha-mcp", "version": "1.0.0", "repository": "https://a"})
    )
    (entries_dir / "alpha-new.json").write_text(
        json.dumps({"name": "alpha-mcp", "version": "1.2.0", "repository": "https://a"})
    )
    (entries_dir / "beta.json").write_text(
        json.dumps({"name": "beta-mcp", "version": "0.1.0", "repository": "https://b"})
    )

    output_file = tmp_path / "marketplace.json"
    aggregate_marketplace_entries(entries_dir, output_file)

    marketplace = json.loads(output_file.read_text())
    assert [item["name"] for item in marketplace["entries"]] == ["alpha-mcp", "beta-mcp"]
    assert marketplace["entries"][0]["version"] == "1.2.0"


def test_resolve_server_args_rewrites_from_to_git_source(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=False)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    resolved = _resolve_server_args(cfg)

    assert resolved[0] == "--from"
    assert resolved[1] == "git+https://github.com/vhspace/example-mcp@v1.2.3"
    assert resolved[2] == "example-mcp"


def test_resolve_server_args_skips_non_github_repos(tmp_path: Path) -> None:
    plugin_path = tmp_path / "mcp-plugin.toml"
    plugin_path.write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://gitlab.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n'
    )
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    resolved = _resolve_server_args(cfg)

    assert resolved[1] == "example-mcp"


def test_generate_claude_plugin_uses_git_source_args(tmp_path: Path) -> None:
    _write_plugin_toml(tmp_path / "mcp-plugin.toml", include_version=False)
    _write_pyproject(tmp_path / "pyproject.toml", include_version=True)
    cfg = load_config(tmp_path)

    generate_claude(cfg, tmp_path)

    plugin = json.loads((tmp_path / ".claude-plugin" / "plugin.json").read_text())
    server_args = plugin["mcpServers"]["example-mcp"]["args"]
    assert "git+https://github.com/vhspace/example-mcp@v1.2.3" in server_args


def _make_repo(root: Path, name: str, version: str = "1.0.0") -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / "mcp-plugin.toml").write_text(
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
    )
    (repo / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n')
    return repo


def test_build_cursor_marketplace_aggregates_plugins(tmp_path: Path) -> None:
    repo_a = _make_repo(tmp_path, "alpha-mcp", "1.0.0")
    repo_b = _make_repo(tmp_path, "beta-mcp", "2.0.0")
    out = tmp_path / "marketplace"

    files = build_cursor_marketplace([repo_a, repo_b], out)

    mp = json.loads((out / ".cursor-plugin" / "marketplace.json").read_text())
    assert mp["name"] == "vhspace-mcp-marketplace"
    assert len(mp["plugins"]) == 2
    assert mp["plugins"][0]["name"] == "alpha-mcp"
    assert mp["plugins"][1]["name"] == "beta-mcp"

    assert (out / "alpha-mcp" / ".cursor-plugin" / "plugin.json").exists()
    assert (out / "beta-mcp" / ".cursor-plugin" / "plugin.json").exists()

    alpha_plugin = json.loads((out / "alpha-mcp" / ".cursor-plugin" / "plugin.json").read_text())
    assert alpha_plugin["version"] == "1.0.0"
    assert (
        "git+https://github.com/vhspace/alpha-mcp@v1.0.0"
        in alpha_plugin["mcpServers"]["alpha-mcp"]["args"]
    )
    assert ".cursor-plugin/marketplace.json" in files


def test_build_cursor_marketplace_skips_invalid_repos(tmp_path: Path) -> None:
    repo_a = _make_repo(tmp_path, "good-mcp")
    bad_repo = tmp_path / "bad-repo"
    bad_repo.mkdir()
    out = tmp_path / "marketplace"

    build_cursor_marketplace([repo_a, bad_repo], out)

    mp = json.loads((out / ".cursor-plugin" / "marketplace.json").read_text())
    assert len(mp["plugins"]) == 1
    assert mp["plugins"][0]["name"] == "good-mcp"
