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

    def test_log_level_normalized_to_uppercase(self) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            settings = MCPSettings()
        assert settings.log_level == "DEBUG"

    def test_debug_from_env(self) -> None:
        with patch.dict(os.environ, {"DEBUG": "true"}):
            settings = MCPSettings()
        assert settings.debug is True
