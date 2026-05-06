"""Asserts that marketplace manifests pin the same git tag as pyproject.toml's package version.

Both `.mcp.json` and `.claude-plugin/plugin.json` use a uvx --from git+...@vX.Y.Z
arg to install the server. If the tag drifts from `[project].version` in
pyproject.toml, fresh marketplace installs run an old version that is missing
tools shipped in newer releases. See vhspace/ufm-mcp#50.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_FILES = [
    REPO_ROOT / ".mcp.json",
    REPO_ROOT / ".claude-plugin" / "plugin.json",
]

# Matches `git+https://github.com/vhspace/ufm-mcp@vX.Y.Z` in the args list.
PIN_RE = re.compile(r"git\+https://github\.com/vhspace/ufm-mcp@v(\d+\.\d+\.\d+)")


def _project_version() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return pyproject["project"]["version"]


@pytest.mark.parametrize("manifest_path", MANIFEST_FILES, ids=lambda p: p.name)
def test_manifest_pin_matches_pyproject_version(manifest_path: Path) -> None:
    text = manifest_path.read_text()
    match = PIN_RE.search(text)
    assert match is not None, (
        f"{manifest_path} has no git+https://github.com/vhspace/ufm-mcp@vX.Y.Z pin. "
        "If the manifest stopped using a git pin (e.g. switched to a PyPI install), "
        "update this test or delete it."
    )
    pinned = match.group(1)
    expected = _project_version()
    assert pinned == expected, (
        f"{manifest_path.name} pins v{pinned} but pyproject.toml [project].version is "
        f"{expected}. Bump the pin (or run release tooling) so marketplace installs match."
    )
