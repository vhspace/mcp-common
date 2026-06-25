"""Inspect AI eval task for awx-mcp in CLI-only mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_awx_scenarios
from _env import apply_resolved_token_to_environ, prepend_repo_bin_to_path
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash
from mcp_common.testing.eval.scorers import cli_tool_use_scorer

from awx_mcp.server import cli_subcommand_map

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "awx-automation" / "SKILL.md"

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant. Use the awx-cli command-line tool to "
    "answer questions about AWX jobs, inventories, and templates.\n\n"
    "Prefer the generic read-only subcommands:\n"
    "- awx-cli list <resource_type> --filter key=value --fields id,name --limit N\n"
    "- awx-cli get <resource_type> <id> --fields id,name,...\n"
    "- awx-cli ping — connectivity/version check\n"
    "Never run launch, sync, cancel, or other mutating commands.\n\n"
    "Here is how to use awx-cli:\n\n{skill_text}"
)


@task
def awx_cli_eval() -> Task:
    """Evaluate agent tool selection and task completion using CLI only."""
    skill_text = _SKILL_PATH.read_text(encoding="utf-8") if _SKILL_PATH.exists() else ""
    apply_resolved_token_to_environ()
    prepend_repo_bin_to_path()
    return Task(
        dataset=load_awx_scenarios(mode_filter={"cli", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT_TEMPLATE.format(skill_text=skill_text)),
            use_tools([bash(timeout=180)]),
            generate(),
        ],
        scorer=cli_tool_use_scorer(
            cli_binary="awx-cli",
            tool_subcommands=cli_subcommand_map(),
            accept_mcp_names=False,
        ),
        message_limit=20,
        sandbox="local",
    )
