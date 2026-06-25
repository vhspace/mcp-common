"""Inspect AI eval task for the cross-cutting CLI-discovery suite (#95).

Targets the agent-onboarding failure mode where an agent asked to "check the
version of the mcp-common CLI tools" spirals on ``mcp-common`` (the library),
``mcp-plugin-gen``, ``mcp-common-doctor``, ``pip show mcp-common``, and
``uv tool list`` instead of running each ``*-cli --version``. The system
prompt embeds the CLI-discovery guidance from
``docs/AGENT_CONVENTIONS.md`` so the eval measures whether agents can follow
that guidance, and the task is scored by the flag-aware, multi-binary
:func:`mcp_common.testing.eval.scorers.cli_discovery_scorer` (the per-binary
``cli_tool_use_scorer`` is blind to a bare ``--version`` — see the issue's
scoring caveat).

No credentials are required: ``--version`` and ``--help`` are eager,
no-creds introspection paths on every ``*-cli``. Do NOT gate this eval on
AWX_TOKEN / NETBOX creds.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset import load_cli_discovery_scenarios
from inspect_ai import Task, task
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import bash
from mcp_common.testing.eval.scorers import cli_discovery_scorer

# Long-form CLI-discovery guidance lives in docs/AGENT_CONVENTIONS.md; the
# tight skill form lives at src/mcp_common/shared_skills/cli-discovery/SKILL.md.
# Embed the skill (agent-runtime trigger) and fall back to a compact inline
# summary if either file is absent at eval time.
_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mcp_common"
    / "shared_skills"
    / "cli-discovery"
    / "SKILL.md"
)
_DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "AGENT_CONVENTIONS.md"

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an infrastructure assistant with access to a bash tool. The "
    "togethercomputer/mcp-common repo ships six companion CLIs, one per "
    "server, each on PATH as a `*-cli` binary.\n\n"
    "Follow this CLI-discovery guidance precisely when answering:\n\n"
    "{discovery_text}\n\n"
    "Use the bash tool to run the `*-cli` commands. `--version` and `--help` "
    "are eager flags that need NO credentials and short-circuit before any "
    "real command, so they work without AWX_TOKEN / NETBOX_TOKEN / etc. Some "
    "CLIs take a few seconds to start (redfish-cli ~6.7s, netbox-cli ~3s) — "
    "wait for the command to return rather than assuming it hangs."
)


def _load_discovery_text() -> str:
    """Prefer the SKILL.md body; fall back to the doc section, then a summary."""
    if _SKILL_PATH.exists():
        return _SKILL_PATH.read_text(encoding="utf-8")
    if _DOC_PATH.exists():
        text = _DOC_PATH.read_text(encoding="utf-8")
        marker = "## CLI discovery: the six `*-cli` tools and `--version`"
        idx = text.find(marker)
        if idx != -1:
            # Trim to the CLI-discovery section (up to the next top-level section).
            tail = text[idx:]
            next_section = tail.find("\n## ", len(marker))
            return tail if next_section == -1 else tail[:next_section]
        return text
    return (
        "The six CLIs are awx-cli, dc-support-cli, network-cli, netbox-cli, "
        "redfish-cli, ufm-cli. Each supports an eager root `--version` flag "
        "(no creds needed) printing the installed package version, and "
        "`--help` lists subcommands. `mcp-common` (the library), "
        "`mcp-plugin-gen`, and `mcp-common-doctor` are NOT version-reporting "
        "CLIs — do not use `pip show mcp-common` / `uv tool list` to answer a "
        "CLI-version question. `ufm-cli --version` prints the CLI package "
        "version; `ufm-cli version` returns live UFM server JSON."
    )


@task
def cli_discovery_eval() -> Task:
    """Evaluate agent CLI-discovery + `--version` reporting across the six `*-cli`."""
    discovery_text = _load_discovery_text()
    return Task(
        dataset=load_cli_discovery_scenarios(mode_filter={"cli", "both"}),
        solver=[
            system_message(_SYSTEM_PROMPT_TEMPLATE.format(discovery_text=discovery_text)),
            use_tools([bash(timeout=180)]),
            generate(),
        ],
        scorer=cli_discovery_scorer(),
        message_limit=20,
        sandbox="local",
    )
