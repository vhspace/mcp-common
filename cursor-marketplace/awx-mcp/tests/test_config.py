"""Tests for configuration management."""

import pytest
from pydantic import ValidationError

from awx_mcp.config import Settings

AWX_ENV_VARS = (
    "AWX_HOST",
    "AWX_TOKEN",
    "CONTROLLER_HOST",
    "CONTROLLER_OAUTH_TOKEN",
    "MCP_HTTP_ACCESS_TOKEN",
    "AWX_MCP_HTTP_ACCESS_TOKEN",
    "AWX_RO_KEY",
    "VERIFY_SSL",
    "TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "TRANSPORT",
    "HOST",
    "PORT",
    "API_BASE_PATH",
)


@pytest.fixture(autouse=True)
def _clean_awx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove AWX-related env vars and prevent .env file from leaking into tests."""
    for var in AWX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "awx_mcp.config.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )


def test_settings_valid_configuration() -> None:
    """Test that valid settings are accepted."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="test-token",
        api_base_path="/api/v2",
        transport="stdio",
        host="127.0.0.1",
        port=8000,
        verify_ssl=True,
        timeout_seconds=30.0,
        log_level="INFO",
    )

    assert str(settings.awx_host) == "https://awx.example.com/"
    assert settings.awx_token.get_secret_value() == "test-token"
    assert settings.api_base_path == "/api/v2"
    assert settings.transport == "stdio"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.verify_ssl is True
    assert settings.timeout_seconds == 30.0
    assert settings.log_level == "INFO"


def test_settings_required_fields() -> None:
    """Test that required fields are enforced."""
    with pytest.raises(ValidationError) as exc_info:
        Settings()  # Missing required fields

    errors = exc_info.value.errors()
    assert len(errors) >= 1  # Should have at least awx_host and awx_token errors


def test_settings_invalid_port() -> None:
    """Test that invalid ports are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            awx_host="https://awx.example.com",
            awx_token="test-token",
            port=70000,  # Invalid port
        )

    errors = exc_info.value.errors()
    assert any("Port must be between 1 and 65535" in str(error) for error in errors)


def test_settings_invalid_api_base_path() -> None:
    """Test that invalid API base paths are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            awx_host="https://awx.example.com",
            awx_token="test-token",
            api_base_path="invalid",  # Missing leading slash
        )

    errors = exc_info.value.errors()
    assert any("API_BASE_PATH must start with '/'" in str(error) for error in errors)


def test_settings_invalid_timeout() -> None:
    """Test that invalid timeouts are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            awx_host="https://awx.example.com",
            awx_token="test-token",
            timeout_seconds=0,  # Invalid timeout
        )

    errors = exc_info.value.errors()
    assert any("TIMEOUT_SECONDS must be > 0" in str(error) for error in errors)


def test_settings_invalid_timeout_negative() -> None:
    """Test that negative timeouts are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            awx_host="https://awx.example.com",
            awx_token="test-token",
            timeout_seconds=-1,  # Invalid timeout
        )

    errors = exc_info.value.errors()
    assert any("TIMEOUT_SECONDS must be > 0" in str(error) for error in errors)


def test_settings_invalid_host_url() -> None:
    """Test that invalid host URLs are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            awx_host="not-a-url",
            awx_token="test-token",
        )

    errors = exc_info.value.errors()
    assert any("url" in str(error).lower() for error in errors)


def test_settings_environment_variable_aliases() -> None:
    """Test that environment variable aliases work."""
    import os

    # Test CONTROLLER_HOST alias
    os.environ["CONTROLLER_HOST"] = "https://controller.example.com"
    os.environ["CONTROLLER_OAUTH_TOKEN"] = "controller-token"

    try:
        settings = Settings()
        assert str(settings.awx_host) == "https://controller.example.com/"
        assert settings.awx_token.get_secret_value() == "controller-token"
    finally:
        del os.environ["CONTROLLER_HOST"]
        del os.environ["CONTROLLER_OAUTH_TOKEN"]


def test_settings_mcp_http_access_token_optional() -> None:
    """Test that MCP_HTTP_ACCESS_TOKEN is optional for stdio transport."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="test-token",
        transport="stdio",
        # No mcp_http_access_token
    )

    assert settings.mcp_http_access_token is None


def test_settings_get_effective_config_summary() -> None:
    """Test that config summary redacts secrets properly."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="secret-token",
        mcp_http_access_token="secret-access-token",
        transport="http",
    )

    summary = settings.get_effective_config_summary()

    # Token should be redacted
    assert summary["awx_token"] == "***REDACTED***"
    assert summary["mcp_http_access_token"] == "***REDACTED***"

    # Other values should be visible
    assert summary["awx_host"] == "https://awx.example.com/"
    assert summary["transport"] == "http"


def test_settings_case_insensitive_env_vars() -> None:
    """Test that environment variables are case-insensitive."""
    import os

    os.environ["AWX_HOST"] = "https://lowercase.example.com"
    os.environ["AWX_TOKEN"] = "uppercase-token"

    try:
        settings = Settings()
        assert str(settings.awx_host) == "https://lowercase.example.com/"
        assert settings.awx_token.get_secret_value() == "uppercase-token"
    finally:
        del os.environ["AWX_HOST"]
        del os.environ["AWX_TOKEN"]


def test_settings_default_values() -> None:
    """Test that default values are applied correctly."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="test-token",
    )

    assert settings.api_base_path == "/api/v2"
    assert settings.transport == "stdio"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.verify_ssl is True
    assert settings.timeout_seconds == 30.0
    assert settings.log_level == "INFO"
