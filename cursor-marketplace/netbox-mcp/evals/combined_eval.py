"""Inspect AI eval task for netbox-mcp in combined mode.

The agent gets both MCP tools AND a bash session, plus the
prefer-cli-over-mcp skill. The combined_scorer checks whether the agent
makes appropriate interface choices (preferring CLI when possible).
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash_session, mcp_server_stdio
from mcp_common.testing.eval.scorers import combined_scorer

from evals._dataset import load_netbox_scenarios

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "netbox-lookups" / "SKILL.md"

_PREFER_CLI_PATHS = [
    Path.home() / ".cursor" / "skills" / "prefer-cli-over-mcp" / "SKILL.md",
    Path(__file__).parent.parent / "skills" / "prefer-cli-over-mcp" / "SKILL.md",
]

_PREFER_CLI_FALLBACK = (
    "When both MCP tools and CLI tools are available, prefer CLI tools for "
    "lower token usage. Use MCP only when the CLI cannot perform the operation. "
    "Run `netbox-cli --help` to discover available commands."
)

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant with access to both NetBox MCP tools "
    "and the netbox-cli command-line tool. Prefer CLI when possible for lower "
    "token usage and faster execution.\n\n"
    "## CLI Usage\n\n{skill_text}\n\n"
    "## Interface Preference\n\n{prefer_cli_text}"
)


def _read_if_exists(
    path: Path | None = None, paths: list[Path] | None = None, fallback: str = ""
) -> str:
    """Read the first existing file from *paths* (or a single *path*), else return *fallback*."""
    candidates = paths if paths is not None else ([path] if path is not None else [])
    for p in candidates:
        if p is not None and p.exists():
            return p.read_text(encoding="utf-8")
    return fallback


@task
def netbox_combined_eval() -> Task:
    """Evaluate agent tool selection, task completion, and interface choice."""
    skill_text = _read_if_exists(path=_SKILL_PATH)
    prefer_cli_text = _read_if_exists(paths=_PREFER_CLI_PATHS, fallback=_PREFER_CLI_FALLBACK)
    return Task(
        dataset=load_netbox_scenarios(mode_filter={"mcp", "cli", "both"}),
        solver=[
            system_message(
                _SYSTEM_PROMPT_TEMPLATE.format(
                    skill_text=skill_text,
                    prefer_cli_text=prefer_cli_text,
                )
            ),
            use_tools(
                [
                    mcp_server_stdio(command="netbox-mcp"),
                    bash_session(timeout=300),
                ]
            ),
            generate(),
        ],
        scorer=combined_scorer(),
        message_limit=20,
    )
