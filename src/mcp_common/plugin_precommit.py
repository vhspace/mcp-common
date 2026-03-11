"""
Pre-commit hook: regenerate plugin configs if mcp-plugin.toml changed.

Usage in .pre-commit-config.yaml:
  - repo: local
    hooks:
      - id: mcp-plugin-gen
        name: mcp-plugin-gen
        entry: uv run mcp-plugin-gen generate .
        language: system
        files: ^mcp-plugin\\.toml$
        pass_filenames: false

Or as a CI check (fails if output is stale):
  uv run mcp-plugin-gen check .
"""

from __future__ import annotations

import filecmp
import json
import tempfile
from pathlib import Path

import typer

from mcp_common.plugin_gen import generate_all, load_config


def check_sync(repo_root: Path) -> tuple[bool, list[str]]:
    """
    Check if generated files match what mcp-plugin-gen would produce.
    Returns (in_sync, list_of_stale_files).
    """
    try:
        cfg = load_config(repo_root)
    except FileNotFoundError:
        return True, []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Copy source files the generator needs
        for skill in cfg.skills:
            src = repo_root / skill.path
            dst = tmp / skill.path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(src, dst)
        for rule in cfg.rules:
            src = repo_root / rule.path
            dst = tmp / rule.path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(src, dst)

        # Also copy mcp-plugin.toml
        import shutil
        shutil.copy2(repo_root / "mcp-plugin.toml", tmp / "mcp-plugin.toml")

        results = generate_all(cfg, tmp)
        stale: list[str] = []

        for _platform, files in results.items():
            for rel_path in files:
                generated = tmp / rel_path
                existing = repo_root / rel_path
                if not existing.exists():
                    stale.append(f"{rel_path} (missing)")
                elif not filecmp.cmp(str(generated), str(existing), shallow=False):
                    stale.append(f"{rel_path} (stale)")

    return len(stale) == 0, stale
