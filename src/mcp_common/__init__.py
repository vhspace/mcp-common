"""Shared utilities and testing infrastructure for MCP server projects."""

from mcp_common.agent_remediation import (
    format_agent_exception_remediation,
    mcp_remediation_wrapper,
    mcp_tool_error_with_remediation,
)
from mcp_common.auth import HttpAccessTokenAuth
from mcp_common.config import MCPSettings
from mcp_common.health import health_resource
from mcp_common.hints import HintRegistry, ToolHint
from mcp_common.http import add_health_route, create_http_app
from mcp_common.logging import setup_logging
from mcp_common.plugin_schema import PluginConfig
from mcp_common.progress import OperationStates, PollResult, poll_with_progress
from mcp_common.sites import SiteConfig, SiteManager
from mcp_common.version import get_version

__all__ = [
    "HintRegistry",
    "HttpAccessTokenAuth",
    "MCPSettings",
    "OperationStates",
    "PluginConfig",
    "PollResult",
    "SiteConfig",
    "SiteManager",
    "ToolHint",
    "add_health_route",
    "create_http_app",
    "format_agent_exception_remediation",
    "get_version",
    "health_resource",
    "mcp_remediation_wrapper",
    "mcp_tool_error_with_remediation",
    "poll_with_progress",
    "setup_logging",
]
