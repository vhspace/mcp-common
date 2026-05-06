"""NetBox MCP Server - Read-only MCP server for NetBox infrastructure data."""

__version__ = "2.10.4"  # Auto-managed by semantic-release

__all__ = ["NETBOX_OBJECT_TYPES", "NetBoxRestClient", "Settings"]

from netbox_mcp.config import Settings
from netbox_mcp.netbox_client import NetBoxRestClient
from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES
