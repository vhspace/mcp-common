"""AWX MCP Server - Model Context Protocol server for Ansible AWX / Automation Controller."""

__version__ = "1.2.0"

__all__ = ["AwxRestClient", "Settings"]

from awx_mcp.awx_client import AwxRestClient
from awx_mcp.config import Settings
