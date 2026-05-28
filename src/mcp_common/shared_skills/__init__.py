"""Staging ground for cross-MCP shared agent skills.

Each subdirectory under :mod:`mcp_common.shared_skills` is a Cursor /
Claude / OpenCode-style ``SKILL.md`` bundle authored once in
``mcp-common`` so every downstream vhspace MCP picks it up via the
shared-skills promotion mechanism tracked in
`vhspace/mcp-common#95 <https://github.com/vhspace/mcp-common/issues/95>`_.

The mechanism itself is unbuilt at the time this directory was created.
Skills land here first; once #95 ships they will be copied into each
MCP's generated plugin tree by ``mcp-plugin-gen``.

This package is a namespace marker — it intentionally has no public API.
"""

__all__: list[str] = []
