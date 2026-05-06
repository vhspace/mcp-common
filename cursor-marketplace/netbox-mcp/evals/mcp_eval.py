"""Inspect AI eval task for netbox-mcp in MCP-only mode.

The agent gets MCP tools via the ``netbox-mcp`` stdio server and must
use them to answer infrastructure questions about NetBox.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import mcp_server_stdio
from mcp_common.testing.eval.scorers import tool_use_scorer

from _dataset import load_netbox_scenarios

_SYSTEM_PROMPT = (
    "You are an infrastructure assistant with access to NetBox via MCP tools. "
    "Use the available tools to answer questions about devices, racks, IPs, "
    "clusters, and other infrastructure objects. Always pass the 'fields' "
    "parameter to minimize token usage."
)


@task
def netbox_mcp_eval() -> Task:
    """Evaluate agent tool selection and task completion using MCP tools only."""
    return Task(
        dataset=load_netbox_scenarios(mode_filter={"mcp", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT),
            use_tools([mcp_server_stdio(
                command="netbox-mcp",
                env={
                    "NETBOX_URL": os.environ.get("NETBOX_URL", ""),
                    "NETBOX_TOKEN": os.environ.get("NETBOX_TOKEN", ""),
                },
            )]),
            generate(),
        ],
        scorer=tool_use_scorer(),
        message_limit=15,
    )
