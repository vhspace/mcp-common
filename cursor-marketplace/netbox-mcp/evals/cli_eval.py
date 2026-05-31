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

from netbox_mcp.server import cli_subcommand_map

# cli_tool_use_scorer (mcp-common >= v0.28.0) credits tool selection by parsing
# the bash command content for ``netbox-cli <subcommand>`` invocations and
# matching them to the scenario's expected MCP tool names. The expected
# subcommand(s) per tool come from ``tool_subcommands`` (the
# ``cli_subcommand_map()`` declared in netbox_mcp.server), so tools whose real
# CLI subcommand differs from the kebab derivation map correctly — e.g.
# ``netbox_get_objects`` -> ``list``/``search``/``devices`` rather than the
# bogus ``get-objects`` (netbox-mcp#125). Undeclared tools fall back to kebab.
# This replaces tool_use_scorer, whose MCP-name match scored CLI runs ~0 because
# the agent calls 'bash', not MCP tools (vhspace/mcp-common#59).
#
# accept_mcp_names=False (the v0.28.0 default; set explicitly here) is correct
# for a CLI-only run: a hallucinated direct MCP call that ran no netbox-cli
# command must NOT earn tool-selection credit (vhspace/mcp-common#133 flipped
# this default True -> False).

_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "netbox-lookups" / "SKILL.md"

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant. Use the netbox-cli command-line "
    "tool to answer questions about devices, racks, IPs, clusters, and "
    "other infrastructure objects.\n\n"
    "Prefer the specialized subcommands over generic listing:\n"
    "- netbox-cli lookup-device <hostname|provider-id|ip> — resolve ONE device; "
    "it also accepts a provider/vendor machine ID and an IP address (the reverse "
    "'which device has IP X?' lookup). Add --site to disambiguate.\n"
    "- netbox-cli oob-summary <device> — BMC / out-of-band IP summary.\n"
    "- netbox-cli get-objects-by-ids dcim.device <id> <id> ... — batch-fetch by ID.\n"
    "- netbox-cli get-object-by-id dcim.device <id> — a single object by ID.\n"
    "- For cluster size / 'how many', report the total 'count' line, not the number "
    "of rows printed (output is paginated; use --limit to fetch all).\n\n"
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
        scorer=cli_tool_use_scorer(
            cli_binary="netbox-cli",
            tool_subcommands=cli_subcommand_map(),
            accept_mcp_names=False,
        ),
        message_limit=20,
        sandbox="local",
    )
