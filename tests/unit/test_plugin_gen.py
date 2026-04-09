"""Tests for mcp-plugin-gen config loading and version sourcing."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_common.plugin_gen import generate_claude, generate_cursor, load_config


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
