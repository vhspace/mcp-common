"""Tests for plugin_cli helpers and commands."""

import json
from pathlib import Path

from typer.testing import CliRunner

from mcp_common.plugin_cli import _referenced_env_vars, app

runner = CliRunner()


def test_referenced_env_vars_extracts_curly_refs_only() -> None:
    refs = _referenced_env_vars(
        {
            "A": "${TOKEN_A}",
            "B": "${TOKEN_B}",
            "C": "literal",
            "D": " ${TOKEN_A} ",
        }
    )
    assert refs == ["TOKEN_A", "TOKEN_B"]


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


def test_registry_entry_command_generates_registry_entry(tmp_path: Path) -> None:
    _write_plugin_repo(tmp_path)
    result = runner.invoke(app, ["registry-entry", str(tmp_path)])

    assert result.exit_code == 0
    output_path = tmp_path / ".claude-plugin" / "registry-entry.json"
    assert output_path.exists()
    entry = json.loads(output_path.read_text())
    assert entry["name"] == "example-mcp"


def test_aggregate_marketplace_command_is_deterministic(tmp_path: Path) -> None:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir(parents=True)
    (entries_dir / "z.json").write_text(
        json.dumps({"name": "zeta-mcp", "version": "1.0.0", "repository": "https://z"})
    )
    (entries_dir / "a1.json").write_text(
        json.dumps({"name": "alpha-mcp", "version": "1.0.0", "repository": "https://a"})
    )
    (entries_dir / "a2.json").write_text(
        json.dumps({"name": "alpha-mcp", "version": "1.2.0", "repository": "https://a"})
    )

    output_path = tmp_path / "marketplace.json"
    result = runner.invoke(app, ["aggregate-marketplace", str(entries_dir), str(output_path)])
    assert result.exit_code == 0

    marketplace = json.loads(output_path.read_text())
    assert [item["name"] for item in marketplace["entries"]] == ["alpha-mcp", "zeta-mcp"]
    assert marketplace["entries"][0]["version"] == "1.2.0"
