"""NetBox MCP Server - Read-only MCP server for NetBox infrastructure data."""

from mcp_common.version import get_version

__version__ = get_version("netbox-mcp")

__all__ = ["NETBOX_OBJECT_TYPES", "NetBoxRestClient", "Settings"]

from netbox_mcp.config import Settings
from netbox_mcp.netbox_client import NetBoxRestClient
from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES
