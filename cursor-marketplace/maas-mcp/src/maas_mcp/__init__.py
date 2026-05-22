"""MAAS MCP Server - Model Context Protocol server for Canonical MAAS."""

from mcp_common.version import get_version

__version__ = get_version("maas-mcp")

__all__ = ["MaasRestClient", "Settings"]

from maas_mcp.config import Settings
from maas_mcp.maas_client import MaasRestClient
