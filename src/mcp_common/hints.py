"""Cross-MCP hint registry.

Allows MCP servers to export their tool surface as structured hints
that other servers can import, keeping cross-references in sync.

Each MCP repo owns a ``hints.py`` module exporting a ``HintRegistry``.
Consumers import hints by ID rather than hardcoding tool names/CLI
commands, getting import-time validation when tools are renamed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolHint:
    """A reference to a tool in another MCP server."""

    name: str
    """Canonical tool/command name (e.g. ``redfish_query``)."""

    description: str
    """Human-readable description of what the tool does."""

    cli_example: str
    """CLI invocation template with ``{placeholders}``."""

    mcp_example: str
    """MCP tool call template with ``{placeholders}``."""

    args: dict[str, str] = field(default_factory=dict)
    """Map of placeholder name to description."""


@dataclass
class HintRegistry:
    """Collection of tool hints exported by an MCP server.

    Usage::

        from redfish_mcp.hints import HINTS as REDFISH

        cli_cmd = REDFISH.format_cli("power_state", host="10.0.0.1")
        mcp_call = REDFISH.format_mcp("screenshot", host="10.0.0.1")
        hint_dict = REDFISH.as_agent_hints(host="<oob_ip>")
    """

    server_name: str
    """Package name of the source MCP server."""

    hints: dict[str, ToolHint]
    """Hints keyed by stable ID (e.g. ``"power_state"``)."""

    def format_cli(self, hint_id: str, **kwargs: str) -> str:
        """Return the CLI example with placeholders filled in."""
        return self.hints[hint_id].cli_example.format(**kwargs)

    def format_mcp(self, hint_id: str, **kwargs: str) -> str:
        """Return the MCP example with placeholders filled in."""
        return self.hints[hint_id].mcp_example.format(**kwargs)

    def as_agent_hints(self, **kwargs: str) -> dict[str, str]:
        """Return all hints as a flat dict suitable for agent responses.

        Keys are hint IDs, values are MCP call templates with
        placeholders filled from ``kwargs``.
        """
        out: dict[str, str] = {}
        for hid, hint in self.hints.items():
            try:
                out[hid] = hint.mcp_example.format(**kwargs)
            except KeyError:
                out[hid] = hint.mcp_example
        return out

    def as_cli_hints(self, **kwargs: str) -> dict[str, str]:
        """Return all hints as a flat dict of CLI commands."""
        out: dict[str, str] = {}
        for hid, hint in self.hints.items():
            try:
                out[hid] = hint.cli_example.format(**kwargs)
            except KeyError:
                out[hid] = hint.cli_example
        return out

    def as_dict(self) -> dict[str, Any]:
        """Serialize the registry to a plain dict."""
        return {
            "server_name": self.server_name,
            "hints": {
                hid: {
                    "name": h.name,
                    "description": h.description,
                    "cli_example": h.cli_example,
                    "mcp_example": h.mcp_example,
                    "args": h.args,
                }
                for hid, h in self.hints.items()
            },
        }
