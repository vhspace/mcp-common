"""Shared utilities and testing infrastructure for MCP server projects."""

from mcp_common.config import MCPSettings
from mcp_common.health import health_resource
from mcp_common.logging import setup_logging
from mcp_common.version import get_version

__all__ = ["MCPSettings", "get_version", "health_resource", "setup_logging"]
