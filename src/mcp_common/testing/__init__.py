"""Testing utilities for MCP server projects.

Install with: uv add "mcp-common[testing]"
"""

from mcp_common.testing.assertions import (
    assert_tool_exists,
    assert_tool_success,
)
from mcp_common.testing.fixtures import mcp_client

__all__ = ["assert_tool_exists", "assert_tool_success", "mcp_client"]
