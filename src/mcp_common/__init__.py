"""Shared utilities and testing infrastructure for MCP server projects."""

from mcp_common.auth import HttpAccessTokenAuth
from mcp_common.config import MCPSettings
from mcp_common.health import health_resource
from mcp_common.http import add_health_route, create_http_app
from mcp_common.logging import setup_logging
from mcp_common.plugin_schema import PluginConfig
from mcp_common.progress import OperationStates, PollResult, poll_with_progress
from mcp_common.version import get_version

__all__ = [
    "HttpAccessTokenAuth",
    "MCPSettings",
    "OperationStates",
    "PluginConfig",
    "PollResult",
    "add_health_route",
    "create_http_app",
    "get_version",
    "health_resource",
    "poll_with_progress",
    "setup_logging",
]
