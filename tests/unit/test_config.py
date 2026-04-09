"""Tests for MCPSettings base configuration."""

import os
from unittest.mock import patch

from mcp_common.config import MCPSettings


class TestMCPSettings:
    def test_defaults(self) -> None:
        settings = MCPSettings()
        assert settings.debug is False
        assert settings.log_level == "INFO"
        assert settings.log_json is False
        assert settings.log_access is True
        assert settings.log_transcript is False
        assert settings.log_transcript_sample_rate == 1.0
        assert settings.log_http_access is False
        assert settings.log_trace_on_error is True
        assert settings.log_trace_include_stack is False
        assert settings.log_request_id_header == "x-request-id"

    def test_log_level_normalized_to_uppercase(self) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            settings = MCPSettings()
        assert settings.log_level == "DEBUG"

    def test_debug_from_env(self) -> None:
        with patch.dict(os.environ, {"DEBUG": "true"}):
            settings = MCPSettings()
        assert settings.debug is True

    def test_github_repo_and_issue_tracker_defaults(self) -> None:
        settings = MCPSettings()
        assert settings.github_repo is None
        assert settings.issue_tracker_url is None

    def test_github_repo_from_env(self) -> None:
        with patch.dict(os.environ, {"GITHUB_REPO": "myorg/my-server"}):
            settings = MCPSettings()
        assert settings.github_repo == "myorg/my-server"

    def test_issue_tracker_url_from_env(self) -> None:
        with patch.dict(os.environ, {"ISSUE_TRACKER_URL": "https://jira.example/browse/PROJ"}):
            settings = MCPSettings()
        assert settings.issue_tracker_url == "https://jira.example/browse/PROJ"
