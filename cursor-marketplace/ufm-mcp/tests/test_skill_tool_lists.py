"""Asserts every `ufm_*` symbol mentioned in any SKILL.md resolves to a registered tool.

`server.py` decorates each public tool with `@dual_mode_tool(mcp, ...)`. Skills
under `skills/*/SKILL.md` document the tools an agent can call. When the two
drift (skill mentions a tool that was renamed or never existed), agents try to
call phantom tools and fail.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "src" / "ufm_mcp" / "server.py"
SKILLS_DIR = REPO_ROOT / "skills"

# Matches bare `ufm_xxx_yyy` symbol references — function names, not arbitrary
# words that happen to start with `ufm_`. Anchored on a non-word character or
# string start so we don't match inside a longer identifier. Also exclude `/`
# in the lookbehind so filesystem paths like `/usr/bin/ufm_ha_watcher` don't
# get mistaken for tool calls.
TOOL_REF_RE = re.compile(r"(?<![A-Za-z0-9_/])(ufm_[a-z][a-z0-9_]*)(?![A-Za-z0-9_])")


def _registered_tool_names() -> set[str]:
    """Parse server.py and return function names registered as MCP tools.

    Recognizes both decoration styles:

    * ``@dual_mode_tool(mcp, ...)`` — the dual-mode framework decorator used by
      every tool after the mcp-common migration → ``ast.Call`` whose ``func`` is
      ``ast.Name(id="dual_mode_tool")``.
    * ``@mcp.tool(...)`` / ``@mcp.tool`` — the plain FastMCP decorator (kept for
      back-compat) → ``ast.Attribute(value=Name(id="mcp"), attr="tool")``.
    """
    tree = ast.parse(SERVER_PY.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            # @dual_mode_tool(mcp, ...) → ast.Call(func=ast.Name(id="dual_mode_tool"))
            if isinstance(target, ast.Name) and target.id == "dual_mode_tool":
                names.add(node.name)
                break
            # @mcp.tool(...) → ast.Attribute(value=Name(id="mcp"), attr="tool")
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
                and target.attr == "tool"
            ):
                names.add(node.name)
                break
    return names


def _skill_tool_references(skill_path: Path) -> set[str]:
    """Extract every `ufm_*` symbol mentioned in a skill file."""
    return set(TOOL_REF_RE.findall(skill_path.read_text()))


def test_registered_tool_set_is_nonempty() -> None:
    """Sanity: if AST parsing breaks, fail loudly here rather than in the real tests."""
    assert len(_registered_tool_names()) >= 30, (
        "Expected ~36 registered tools; AST parse may be broken."
    )


def test_every_skill_tool_reference_is_registered() -> None:
    registered = _registered_tool_names()

    drift: dict[str, set[str]] = {}
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        referenced = _skill_tool_references(skill_md)
        unknown = referenced - registered
        if unknown:
            drift[str(skill_md.relative_to(REPO_ROOT))] = unknown

    assert not drift, (
        "Skill files reference ufm_* symbols that are not registered as @mcp.tool in server.py:\n"
        + "\n".join(f"  {path}: {sorted(missing)}" for path, missing in sorted(drift.items()))
        + "\nEither the symbol was renamed (update the skill) or it never existed (remove it)."
    )
