"""
redfish_mcp

MCP server for Redfish operations with comprehensive tooling.
"""

from mcp_common import get_version

# Single source of truth is pyproject.toml [project].version, resolved at
# runtime from the installed package metadata via mcp_common.get_version.
__version__ = get_version("redfish-mcp")
