"""AWX MCP Server - Model Context Protocol server for Ansible AWX / Automation Controller."""

from mcp_common.version import get_version

__version__ = get_version("awx-mcp")

__all__ = ["AwxRestClient", "Settings"]

from awx_mcp.awx_client import AwxRestClient
from awx_mcp.config import Settings
