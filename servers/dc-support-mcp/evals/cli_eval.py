"""Inspect AI eval task for dc-support-mcp in CLI-only mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_dc_support_scenarios
from _env import apply_resolved_secrets_to_environ, prepend_repo_bin_to_path
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash
from mcp_common.testing.eval.scorers import cli_tool_use_scorer

from dc_support_mcp.mcp_server import cli_subcommand_map

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "vendor-support" / "SKILL.md"

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant. Use the dc-support-cli command-line tool "
    "to inspect vendor support portals and credential status.\n\n"
    "Prefer these read-only subcommands:\n"
    "- dc-support-cli auth-status --vendor <ori|hypertec|iren> [--json]\n"
    "- dc-support-cli tickets --vendor ori --status open --limit N\n"
    "- dc-support-cli get-ticket <id> --vendor ori\n"
    "- dc-support-cli vendors — credential configuration overview\n"
    "Never run triage, comment, update-ticket, create-service-request, silence, "
    "set-active, or other mutating commands.\n\n"
    "Here is how to use dc-support-cli:\n\n{skill_text}"
)


@task
def dc_support_cli_eval() -> Task:
    """Evaluate agent tool selection and task completion using CLI only."""
    skill_text = _SKILL_PATH.read_text(encoding="utf-8") if _SKILL_PATH.exists() else ""
    apply_resolved_secrets_to_environ()
    prepend_repo_bin_to_path()
    return Task(
        dataset=load_dc_support_scenarios(mode_filter={"cli", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT_TEMPLATE.format(skill_text=skill_text)),
            use_tools([bash(timeout=180)]),
            generate(),
        ],
        scorer=cli_tool_use_scorer(
            cli_binary="dc-support-cli",
            tool_subcommands=cli_subcommand_map(),
            accept_mcp_names=False,
        ),
        message_limit=20,
        sandbox="local",
    )
