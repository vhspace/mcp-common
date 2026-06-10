"""Staging ground for cross-MCP shared agent skills.

Each subdirectory under :mod:`mcpanvil.shared_skills` is a Cursor /
Claude / OpenCode-style ``SKILL.md`` bundle authored once in
``mcpanvil`` so every downstream MCP server picks it up via the
shared-skills promotion mechanism.

Skills land here first; they are copied into each MCP's generated plugin
tree by ``mcp-plugin-gen``.

This package is a namespace marker — it intentionally has no public API.
"""

__all__: list[str] = []
