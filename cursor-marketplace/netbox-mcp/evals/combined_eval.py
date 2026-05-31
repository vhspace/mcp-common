"""Inspect AI eval task for netbox-mcp in combined mode.

The agent gets both MCP tools AND a bash session, plus the
prefer-cli-over-mcp skill. ``cli_tool_use_scorer`` (mcp-common >= v0.28.0)
credits tool selection whether the agent answered via a ``netbox-cli``
subcommand or a direct MCP tool call, so the CLI half is no longer scored ~0
the way the MCP-name match did (vhspace/mcp-common#59).

Two scorer knobs matter here:

* ``tool_subcommands=cli_subcommand_map()`` — maps each expected MCP tool to
  its real ``netbox-cli`` subcommand(s) (e.g. ``netbox_get_objects`` ->
  ``list``/``search``/``devices``) instead of the bogus kebab ``get-objects``
  (netbox-mcp#125).
* ``accept_mcp_names=True`` — set explicitly because v0.28.0 flipped the
  default to ``False`` (vhspace/mcp-common#133). A combined run SHOULD still
  credit a direct MCP tool call, so we opt back in.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_netbox_scenarios
from _netbox_env import apply_resolved_token_to_environ, netbox_mcp_env
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash, mcp_server_stdio
from mcp_common.testing.eval.scorers import cli_tool_use_scorer

from netbox_mcp.server import cli_subcommand_map

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
    """Evaluate agent tool selection (CLI subcommand or MCP tool) and task completion."""
    skill_text = _read_if_exists(path=_SKILL_PATH)
    prefer_cli_text = _read_if_exists(paths=_PREFER_CLI_PATHS, fallback=_PREFER_CLI_FALLBACK)
    # Export the resolved plain token into os.environ so the bash half (which
    # inherits the parent env) runs netbox-cli with a usable token too — the MCP
    # child gets a plain token via netbox_mcp_env() (netbox-mcp#117).
    apply_resolved_token_to_environ()
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
                    mcp_server_stdio(
                        command="netbox-mcp",
                        env=netbox_mcp_env(),
                    ),
                    # One-shot ``bash`` (not interactive ``bash_session``) for
                    # the CLI half — see cli_eval.py for the rationale
                    # (netbox-mcp#138: weak models hallucinated a ``netbox-cli``
                    # tool / never issued the follow-up ``read`` on a session).
                    bash(timeout=180),
                ]
            ),
            generate(),
        ],
        scorer=cli_tool_use_scorer(
            cli_binary="netbox-cli",
            tool_subcommands=cli_subcommand_map(),
            accept_mcp_names=True,
        ),
        message_limit=20,
    )
