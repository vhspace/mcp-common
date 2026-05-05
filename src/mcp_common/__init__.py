"""Shared utilities and testing infrastructure for MCP server projects."""

from mcp_common.agent_remediation import (
    format_agent_exception_remediation,
    mcp_remediation_wrapper,
    mcp_tool_error_with_remediation,
)
from mcp_common.auth import HttpAccessTokenAuth
from mcp_common.config import MCPSettings
from mcp_common.credentials import (
    CredentialAuditEvent,
    CredentialCandidate,
    CredentialResult,
    UsernamePassword,
    UsernamePasswordCredentialProvider,
)
from mcp_common.env import load_env
from mcp_common.health import health_resource
from mcp_common.hints import HintRegistry, ToolHint
from mcp_common.http import add_health_route, create_http_app
from mcp_common.logging import (
    LOG_CHANNEL_ACCESS,
    LOG_CHANNEL_APP,
    LOG_CHANNEL_TRACE,
    LOG_CHANNEL_TRANSCRIPT,
    compute_error_fingerprint,
    format_exception_for_trace,
    log_access_event,
    log_timing_event,
    log_trace_event,
    log_transcript_event,
    mcp_log_access,
    mcp_log_trace,
    mcp_log_transcript,
    redact_config_from_settings,
    sanitize_transcript_value,
    setup_logging,
    suppress_ssl_warnings,
    timed_operation,
    transcript_should_log,
)
from mcp_common.plugin_schema import PluginConfig
from mcp_common.progress import OperationStates, PollResult, poll_with_progress
from mcp_common.sites import SiteConfig, SiteManager
from mcp_common.version import get_version

__all__ = [
    "LOG_CHANNEL_ACCESS",
    "LOG_CHANNEL_APP",
    "LOG_CHANNEL_TRACE",
    "LOG_CHANNEL_TRANSCRIPT",
    "CredentialAuditEvent",
    "CredentialCandidate",
    "CredentialResult",
    "HintRegistry",
    "HttpAccessTokenAuth",
    "MCPSettings",
    "OperationStates",
    "PluginConfig",
    "PollResult",
    "SiteConfig",
    "SiteManager",
    "ToolHint",
    "UsernamePassword",
    "UsernamePasswordCredentialProvider",
    "add_health_route",
    "compute_error_fingerprint",
    "create_http_app",
    "format_agent_exception_remediation",
    "format_exception_for_trace",
    "get_version",
    "health_resource",
    "load_env",
    "log_access_event",
    "log_timing_event",
    "log_trace_event",
    "log_transcript_event",
    "mcp_log_access",
    "mcp_log_trace",
    "mcp_log_transcript",
    "mcp_remediation_wrapper",
    "mcp_tool_error_with_remediation",
    "poll_with_progress",
    "redact_config_from_settings",
    "sanitize_transcript_value",
    "setup_logging",
    "suppress_ssl_warnings",
    "timed_operation",
    "transcript_should_log",
]
