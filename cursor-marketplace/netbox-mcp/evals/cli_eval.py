"""Inspect AI eval task for netbox-mcp in CLI-only mode.

The agent gets a bash session and the netbox-cli skill as a system prompt.
It must use ``netbox-cli`` shell commands to answer infrastructure questions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_netbox_scenarios
from _netbox_env import apply_resolved_token_to_environ
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash_session
from mcp_common.testing.eval.scorers import cli_tool_use_scorer

# cli_tool_use_scorer (mcp-common >= v0.27.0) credits tool selection by parsing
# the bash command content for ``netbox-cli <subcommand>`` invocations and
# mapping the scenario's expected MCP tool names to CLI subcommands via the
# dual-mode naming convention (e.g. netbox_lookup_device -> lookup-device). This
# replaces tool_use_scorer, whose MCP-name match scored CLI runs ~0 because the
# agent calls 'bash', not MCP tools (vhspace/mcp-common#59).

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
    # The bash sandbox inherits the parent os.environ, so resolve NETBOX_TOKEN
    # (op:// -> plain) here and export it; netbox-cli then needs no 1Password
    # access in the sandbox (netbox-mcp#117).
    apply_resolved_token_to_environ()
    return Task(
        dataset=load_netbox_scenarios(mode_filter={"cli", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT_TEMPLATE.format(skill_text=skill_text)),
            use_tools([bash_session(timeout=300)]),
            generate(),
        ],
        scorer=cli_tool_use_scorer(cli_binary="netbox-cli"),
        message_limit=20,
        sandbox="local",
    )
