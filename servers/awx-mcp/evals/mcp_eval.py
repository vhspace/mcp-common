"""Inspect AI eval task for awx-mcp in MCP-only mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_awx_scenarios
from _env import awx_mcp_command, awx_mcp_env
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import mcp_server_stdio
from mcp_common.testing.eval import read_only_tools
from mcp_common.testing.eval.scorers import tool_use_scorer

_SYSTEM_PROMPT = (
    "You are an infrastructure assistant with access to Ansible AWX via MCP tools. "
    "Use the available read-only tools to answer questions about jobs, inventories, "
    "templates, and other AWX resources. Always pass the 'fields' parameter and keep "
    "page_size small to minimize token usage.\n"
    "Tool selection:\n"
    "- To list or search resources (jobs, inventories, templates, hosts, …), use "
    "awx_list_resources with resource_type and filters — never awx_launch.\n"
    "- To fetch one object by ID, use awx_get_resource.\n"
    "- For connectivity/version checks, use awx_ping.\n"
    "- awx_list_supported_resources shows which resource types exist.\n"
    "This eval is read-only: do not launch jobs or create/update/delete resources."
)

# Explicit allow-list — write/launch tools must never reach the model under test.
_READ_ONLY_MCP_TOOLS = [
    "awx_ping",
    "awx_get_me",
    "awx_list_supported_resources",
    "awx_list_resources",
    "awx_get_resource",
    "awx_get_job_stdout",
    "awx_get_job_results",
    "awx_parse_job_log",
    "awx_debug_job_template_credentials",
    "awx_list_aws_like_credentials",
    "awx_get_workflow_visualization",
    "awx_get_system_info",
    "awx_get_cluster_status",
    "awx_get_system_metrics",
]


@task
def awx_mcp_eval() -> Task:
    """Evaluate agent tool selection and task completion using MCP tools only."""
    return Task(
        dataset=load_awx_scenarios(mode_filter={"mcp", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT),
            use_tools(
                [
                    read_only_tools(
                        mcp_server_stdio(
                            command=awx_mcp_command(),
                            env=awx_mcp_env(),
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
