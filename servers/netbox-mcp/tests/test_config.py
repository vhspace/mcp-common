"""Tests for configuration management."""

import logging
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from netbox_mcp.config import Settings, suppress_noisy_loggers
from netbox_mcp.server import _parse_cli_args as parse_cli_args


def test_settings_requires_netbox_url():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValidationError, match="netbox_url"):
            Settings(netbox_token="test-token", _env_file=None)


def test_settings_requires_netbox_token():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValidationError, match="netbox_token"):
            Settings(netbox_url="https://netbox.example.com/", _env_file=None)


def test_settings_validates_url_format():
    with pytest.raises(ValidationError, match="Input should be a valid URL"):
        Settings(netbox_url="not-a-valid-url", netbox_token="test-token")


def test_settings_validates_port_range():
    with pytest.raises(ValidationError, match="port"):
        Settings(
            netbox_url="https://netbox.example.com/",
            netbox_token="test-token",
            port=99999,
        )


def test_settings_masks_secrets_in_summary():
    settings = Settings(netbox_url="https://netbox.example.com/", netbox_token="super-secret-token")
    summary = settings.get_effective_config_summary()
    assert summary["netbox_token"] == "***REDACTED***"
    assert "super-secret-token" not in str(summary)


def test_settings_inherits_mcp_common_fields():
    settings = Settings(
        netbox_url="https://netbox.example.com/",
        netbox_token="test-token",
        log_json=True,
        debug=True,
    )
    assert settings.log_json is True
    assert settings.debug is True
    assert settings.log_level == "INFO"


def test_settings_log_level_normalized_to_upper():
    settings = Settings(
        netbox_url="https://netbox.example.com/",
        netbox_token="test-token",
        log_level="debug",
    )
    assert settings.log_level == "DEBUG"


# ===== CLI Argument Parsing Tests =====


def test_parse_cli_args_multiple():
    original_argv = sys.argv
    try:
        sys.argv = [
            "server.py",
            "--netbox-url",
            "https://test.example.com/",
            "--transport",
            "http",
            "--port",
            "9000",
            "--log-level",
            "DEBUG",
            "--no-verify-ssl",
        ]
        result = parse_cli_args()
        assert result["netbox_url"] == "https://test.example.com/"
        assert result["transport"] == "http"
        assert result["port"] == 9000
        assert result["log_level"] == "DEBUG"
        assert result["verify_ssl"] is False
    finally:
        sys.argv = original_argv


# ===== Logging Configuration Tests =====


def test_suppress_noisy_loggers_at_info():
    suppress_noisy_loggers("INFO")
    for name in ("urllib3", "httpx", "requests"):
        assert logging.getLogger(name).level == logging.WARNING


def test_suppress_noisy_loggers_at_debug():
    suppress_noisy_loggers("DEBUG")
    for name in ("urllib3", "httpx", "requests"):
        assert logging.getLogger(name).level == logging.DEBUG
