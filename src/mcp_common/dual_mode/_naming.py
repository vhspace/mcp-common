"""Tool-name → CLI-name conversion helpers shared by decorator and builder."""

from __future__ import annotations

import re

__all__ = ["derive_cli_name", "strip_mcp_namespace", "to_kebab_case"]


def to_kebab_case(name: str) -> str:
    """Convert ``snake_case``/``camelCase``/``PascalCase`` to ``kebab-case``.

    Behavior is deliberately conservative: snake-case underscores are
    converted to dashes, camel/pascal boundaries are split on the
    lower→upper transition, and the result is lowercased. Already-kebab-
    cased input passes through unchanged.
    """
    # camelCase / PascalCase boundary: insert dash between aB
    with_dashes = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    # Replace remaining underscores and lowercase the whole thing
    return with_dashes.replace("_", "-").lower()


def strip_mcp_namespace(tool_name: str, mcp_name: str) -> str:
    """Strip the FastMCP instance's name prefix from ``tool_name`` if present.

    E.g. ``mcp_name="netbox"`` strips ``"netbox_"`` off
    ``"netbox_lookup_device"`` → ``"lookup_device"``.

    Matching tolerates kebab-/snake-case variants of ``mcp_name`` (so a
    server named ``"netbox-mcp"`` strips ``"netbox_mcp_"`` /
    ``"netbox-mcp-"`` / ``"netboxmcp_"``). Comparison is case-insensitive.
    """
    if not mcp_name:
        return tool_name
    normalized_mcp = mcp_name.lower().replace("-", "").replace("_", "")
    candidates = {
        mcp_name.lower(),
        mcp_name.lower().replace("-", "_"),
        mcp_name.lower().replace("_", "-"),
        normalized_mcp,
    }
    lowered = tool_name.lower()
    for candidate in candidates:
        for sep in ("_", "-"):
            prefix = f"{candidate}{sep}"
            if lowered.startswith(prefix) and len(tool_name) > len(prefix):
                return tool_name[len(prefix) :]
    return tool_name


def derive_cli_name(tool_name: str, mcp_name: str) -> str:
    """Default CLI command name derived from ``tool_name``.

    Strips the FastMCP instance name prefix when present and kebab-cases the
    remainder. ``netbox_lookup_device`` on ``mcp_name="netbox"`` becomes
    ``lookup-device``.
    """
    stripped = strip_mcp_namespace(tool_name, mcp_name)
    return to_kebab_case(stripped)
