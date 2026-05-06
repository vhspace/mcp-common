"""MAAS MCP Server - Model Context Protocol server for Canonical MAAS."""

__version__ = "1.18.0"

__all__ = ["MaasRestClient", "Settings"]

from maas_mcp.config import Settings
from maas_mcp.maas_client import MaasRestClient
