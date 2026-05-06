"""Inspect AI eval task for netbox-mcp in CLI-only mode.

The agent gets a bash session and the netbox-cli skill as a system prompt.
It must use ``netbox-cli`` shell commands to answer infrastructure questions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash_session
from mcp_common.testing.eval.scorers import tool_use_scorer

from _dataset import load_netbox_scenarios

# Note: tool_use_scorer's deterministic tool selection check will score 0.0
# for CLI evals since the agent calls 'bash', not MCP tool names.
# The LLM-as-judge task completion score is the meaningful metric here.

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "netbox-lookups" / "SKILL.md"

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant. Use the netbox-cli command-line "
    "tool to answer questions about devices, racks, IPs, clusters, and "
    "other infrastructure objects.\n\n"
    "Here is how to use netbox-cli:\n\n{skill_text}"
)


@task
def netbox_cli_eval() -> Task:
    """Evaluate agent tool selection and task completion using CLI only."""
    skill_text = _SKILL_PATH.read_text(encoding="utf-8") if _SKILL_PATH.exists() else ""
    return Task(
        dataset=load_netbox_scenarios(mode_filter={"cli", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT_TEMPLATE.format(skill_text=skill_text)),
            use_tools([bash_session(timeout=300)]),
            generate(),
        ],
        scorer=tool_use_scorer(),
        message_limit=20,
        sandbox="local",
    )
