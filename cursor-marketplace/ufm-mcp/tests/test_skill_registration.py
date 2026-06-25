"""Every skills/<name>/SKILL.md dir must be registered in mcp-plugin.toml.

``mcp-plugin-gen`` only emits skills listed under ``[[skills]]`` (keyed by
``name``, sourced from ``path``); an unregistered skill dir is silently skipped
and never reaches agents. This guards the post-migration under-registration
(mcp-common #85) where ufm-opensm-restart was absent and the fabric skill had a
name/path mismatch. The convention (netbox/awx) is: one entry per skill dir with
``name`` == dir basename and ``path`` == ``skills/<dir>/SKILL.md``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_TOML = REPO_ROOT / "mcp-plugin.toml"
SKILLS_DIR = REPO_ROOT / "skills"


def _registered_skills() -> list[dict]:
    data = tomllib.loads(PLUGIN_TOML.read_text())
    return data.get("skills", [])


def _skill_dirs() -> list[str]:
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))


def _frontmatter_name(skill_md: Path) -> str | None:
    text = skill_md.read_text()
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    for line in parts[1].splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip()
    return None


def test_every_skill_dir_is_registered() -> None:
    registered = {s["name"] for s in _registered_skills()}
    missing = [d for d in _skill_dirs() if d not in registered]
    assert not missing, (
        f"skills/ dirs not registered in mcp-plugin.toml [[skills]]: {missing}. "
        "Add a [[skills]] entry (name == dir, path == skills/<dir>/SKILL.md) or "
        "mcp-plugin-gen skips them and the skills never reach agents."
    )


def test_registered_skill_name_matches_path() -> None:
    for skill in _registered_skills():
        expected = f"skills/{skill['name']}/SKILL.md"
        assert skill["path"] == expected, (
            f"skill '{skill['name']}' path {skill['path']!r} != {expected!r}; "
            "name and path must agree (netbox/awx convention)."
        )


def test_registered_skill_paths_exist() -> None:
    for skill in _registered_skills():
        assert (REPO_ROOT / skill["path"]).is_file(), (
            f"skill '{skill['name']}' path does not exist: {skill['path']}"
        )


def test_skill_frontmatter_name_matches_dir() -> None:
    mismatches: dict[str, str | None] = {}
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        dir_name = skill_md.parent.name
        fm_name = _frontmatter_name(skill_md)
        if fm_name != dir_name:
            mismatches[dir_name] = fm_name
    assert not mismatches, (
        "SKILL.md frontmatter `name` must match its directory "
        f"(dir -> frontmatter name): {mismatches}"
    )
