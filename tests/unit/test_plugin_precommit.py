"""Tests for plugin pre-commit sync behavior."""

from __future__ import annotations

from pathlib import Path

from mcp_common.plugin_gen import generate_all, load_config
from mcp_common.plugin_precommit import check_sync


def _write_plugin_repo(root: Path) -> None:
    (root / "mcp-plugin.toml").write_text(
        'name = "example-mcp"\n'
        'description = "Example MCP server"\n'
        'repository = "https://github.com/vhspace/example-mcp"\n'
        'license = "Apache-2.0"\n'
        'keywords = ["mcp"]\n\n'
        "[author]\n"
        'name = "Together AI"\n\n'
        "[server]\n"
        'command = "uvx"\n'
        'args = ["--from", "example-mcp", "example-mcp"]\n'
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "example-mcp"\n'
        'version = "1.2.3"\n'
        'description = "Example MCP server"\n'
        'requires-python = ">=3.12"\n'
    )


def test_check_sync_detects_missing_registry_entry(tmp_path: Path) -> None:
    _write_plugin_repo(tmp_path)
    cfg = load_config(tmp_path)
    generate_all(cfg, tmp_path)

    (tmp_path / ".claude-plugin" / "registry-entry.json").unlink()
    in_sync, stale = check_sync(tmp_path)

    assert not in_sync
    assert ".claude-plugin/registry-entry.json (missing)" in stale
