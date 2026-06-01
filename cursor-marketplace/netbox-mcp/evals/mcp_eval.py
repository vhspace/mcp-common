"""Inspect AI eval task for netbox-mcp in MCP-only mode.

The agent gets MCP tools via the ``netbox-mcp`` stdio server and must
use them to answer infrastructure questions about NetBox.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_netbox_scenarios
from _netbox_env import netbox_mcp_command, netbox_mcp_env
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import mcp_server_stdio
from mcp_common.testing.eval import read_only_tools
from mcp_common.testing.eval.scorers import tool_use_scorer

_SYSTEM_PROMPT = (
    "You are an infrastructure assistant with access to NetBox via MCP tools. "
    "Use the available tools to answer questions about devices, racks, IPs, "
    "clusters, and other infrastructure objects. Always pass the 'fields' "
    "parameter to minimize token usage.\n"
    "Tool selection:\n"
    "- To resolve a SINGLE device — by hostname, by a provider/vendor machine ID, "
    "or by an IP address — use netbox_lookup_device. It falls back to the "
    "Provider_Machine_ID custom field and then to an IPAM lookup, so it also "
    "answers reverse 'which device has IP X?' questions; don't give up just "
    "because the query is not a Together hostname.\n"
    "- For BMC / out-of-band questions, use netbox_oob_summary.\n"
    "- To fetch several objects by ID, use netbox_get_objects_by_ids in one "
    "batched call rather than repeating netbox_get_object_by_id.\n"
    "- To count or list a whole cluster/site, filter by the resolved "
    "cluster_id/site_id (not a name/text search, which over-matches) and report "
    "the response 'count' (the full total) — results are paginated, so do not "
    "report only the first page."
)

# Read-only tools exposed to the model under test. The netbox-mcp server also
# registers a WRITE tool (``netbox_update_device``, tagged ``{"write","dcim"}``)
# and ``netbox_get_changelogs``; neither is ever an ``expected_tool`` in the
# read-only scenarios. Exposing a write verb to a small/fast model is a
# selection/safety hazard and the surplus tools are documented mis-selection
# traps (netbox-mcp#122, evidence in #121: Qwen3.5-9B weakest at tool
# selection). Restrict the surface to just these read-only tools via the
# shared ``read_only_tools`` helper (mcp-common v0.29.0, #131) — an allow-list
# guarantees exactly this 6-tool set regardless of what the server registers,
# and pushes the allow-list down into inspect's native ``mcp_tools(server,
# tools=[...])`` filter, so the effective surface is identical to the prior
# inline filter (#122).
_READ_ONLY_MCP_TOOLS = [
    "netbox_lookup_device",
    "netbox_oob_summary",
    "netbox_get_objects",
    "netbox_get_object_by_id",
    "netbox_get_objects_by_ids",
    "netbox_search_objects",
]


@task
def netbox_mcp_eval() -> Task:
    """Evaluate agent tool selection and task completion using MCP tools only."""
    return Task(
        dataset=load_netbox_scenarios(mode_filter={"mcp", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT),
            # Resolve NETBOX_TOKEN in THIS (parent) process and forward a plain
            # token to the spawned child. The child gets no op/1Password access,
            # so forwarding an op:// reference would fail credential_chain
            # (netbox-mcp#117; prior env-forwarding #108/#109).
            use_tools(
                [
                    read_only_tools(
                        mcp_server_stdio(
                            # Absolute path to the REPO's current netbox-mcp
                            # console script (the eval venv's bin), NOT a bare
                            # "netbox-mcp" that $PATH could resolve to a stale
                            # global build (netbox-mcp#137). The version
                            # preflight asserts this is the working-tree build.
                            command=netbox_mcp_command(),
                            env=netbox_mcp_env(),
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
