"""Inspect AI eval task for dc-support-mcp in MCP-only mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_dc_support_scenarios
from _env import dc_support_mcp_command, dc_support_mcp_env
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import mcp_server_stdio
from mcp_common.testing.eval import read_only_tools
from mcp_common.testing.eval.scorers import tool_use_scorer

_SYSTEM_PROMPT = (
    "You are an infrastructure assistant with access to datacenter vendor support "
    "portals via MCP tools. Use the available read-only tools to inspect tickets "
    "and knowledge-base articles. Keep limits small to minimize token usage.\n"
    "Tool selection:\n"
    "- To list tickets, use list_vendor_tickets with vendor and status filters.\n"
    "- To fetch one ticket, use get_vendor_ticket with include_comments=False when "
    "comments are not needed.\n"
    "- For KB search/read, use search_vendor_kb / get_vendor_kb_article.\n"
    "- list_rtb_triage_tickets lists internal triage tickets (read-only).\n"
    "This eval is read-only: do NOT create tickets, post comments, update status, "
    "silence alerts, or run any mutating operation."
)

# Explicit allow-list — write tools must never reach the model under test.
_READ_ONLY_MCP_TOOLS = [
    "get_vendor_ticket",
    "list_vendor_tickets",
    "list_rtb_triage_tickets",
    "search_vendor_kb",
    "get_vendor_kb_article",
]


@task
def dc_support_mcp_eval() -> Task:
    """Evaluate agent tool selection and task completion using MCP tools only."""
    return Task(
        dataset=load_dc_support_scenarios(mode_filter={"mcp", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT),
            use_tools(
                [
                    read_only_tools(
                        mcp_server_stdio(
                            command=dc_support_mcp_command(),
                            env=dc_support_mcp_env(),
                        ),
                        allow=_READ_ONLY_MCP_TOOLS,
                    )
                ]
            ),
            generate(),
        ],
        scorer=tool_use_scorer(),
        message_limit=15,
    )
