"""Tests for configuration management."""

import pytest
from mcp_common import MCPSettings
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
    "DEBUG",
    "LOG_JSON",
    "STATELESS_HTTP",
    "AWX_MCP_DEBUG",
    "AWX_MCP_AWX_HOST",
    "AWX_MCP_AWX_TOKEN",
    "AWX_MCP_CONTROLLER_HOST",
    "AWX_MCP_CONTROLLER_OAUTH_TOKEN",
    "AWX_MCP_API_BASE_PATH",
    "AWX_MCP_VERIFY_SSL",
    "AWX_MCP_TIMEOUT_SECONDS",
    "AWX_MCP_LOG_LEVEL",
    "AWX_MCP_LOG_JSON",
    "AWX_MCP_TRANSPORT",
    "AWX_MCP_HOST",
    "AWX_MCP_PORT",
    "AWX_MCP_STATELESS_HTTP",
    "AWX_MCP_MCP_HTTP_ACCESS_TOKEN",
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


def test_settings_inherits_mcp_settings_standard_fields() -> None:
    """Test that AWX settings adopt MCPSettings standard fields."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="test-token",
        debug=True,
        log_json=True,
        stateless_http=False,
    )

    assert issubclass(Settings, MCPSettings)
    assert settings.debug is True
    assert settings.log_json is True
    assert settings.stateless_http is False


def test_settings_supports_mcp_settings_prefixed_standard_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that AWX_MCP_ prefixed MCPSettings env vars are supported."""
    monkeypatch.setenv("AWX_HOST", "https://awx.example.com")
    monkeypatch.setenv("AWX_TOKEN", "test-token")
    monkeypatch.setenv("AWX_MCP_DEBUG", "true")
    monkeypatch.setenv("AWX_MCP_LOG_LEVEL", "debug")
    monkeypatch.setenv("AWX_MCP_LOG_JSON", "true")
    monkeypatch.setenv("AWX_MCP_TRANSPORT", "http")
    monkeypatch.setenv("AWX_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("AWX_MCP_PORT", "9000")
    monkeypatch.setenv("AWX_MCP_STATELESS_HTTP", "false")
    monkeypatch.setenv("AWX_MCP_MCP_HTTP_ACCESS_TOKEN", "prefixed-http-token")

    settings = Settings()

    assert settings.debug is True
    assert settings.log_level == "DEBUG"
    assert settings.log_json is True
    assert settings.transport == "http"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.stateless_http is False
    assert settings.mcp_http_access_token is not None
    assert settings.mcp_http_access_token.get_secret_value() == "prefixed-http-token"


def test_settings_supports_prefixed_controller_env_var_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that AWX_MCP_ prefixed Controller aliases are symmetric."""
    monkeypatch.setenv("AWX_MCP_CONTROLLER_HOST", "https://controller.example.com")
    monkeypatch.setenv("AWX_MCP_CONTROLLER_OAUTH_TOKEN", "controller-token")

    settings = Settings()

    assert str(settings.awx_host) == "https://controller.example.com/"
    assert settings.awx_token.get_secret_value() == "controller-token"


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


def test_settings_preserves_documented_unprefixed_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that documented env vars keep working with the AWX_MCP_ prefix configured."""
    monkeypatch.setenv("AWX_HOST", "https://awx.example.com")
    monkeypatch.setenv("AWX_TOKEN", "test-token")
    monkeypatch.setenv("API_BASE_PATH", "/custom/api/")
    monkeypatch.setenv("TRANSPORT", "http")
    monkeypatch.setenv("HOST", "127.0.0.2")
    monkeypatch.setenv("PORT", "8111")
    monkeypatch.setenv("MCP_HTTP_ACCESS_TOKEN", "http-token")
    monkeypatch.setenv("VERIFY_SSL", "false")
    monkeypatch.setenv("TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LOG_LEVEL", "warning")

    settings = Settings()

    assert str(settings.awx_host) == "https://awx.example.com/"
    assert settings.awx_token.get_secret_value() == "test-token"
    assert settings.api_base_path == "/custom/api"
    assert settings.transport == "http"
    assert settings.host == "127.0.0.2"
    assert settings.port == 8111
    assert settings.mcp_http_access_token is not None
    assert settings.mcp_http_access_token.get_secret_value() == "http-token"
    assert settings.verify_ssl is False
    assert settings.timeout_seconds == 12.5
    assert settings.log_level == "WARNING"


def test_settings_mcp_http_access_token_optional() -> None:
    """Test that MCP_HTTP_ACCESS_TOKEN is optional for stdio transport."""
    settings = Settings(
        awx_host="https://awx.example.com",
        awx_token="test-token",
        transport="stdio",
        # No mcp_http_access_token
    )

    assert settings.mcp_http_access_token is None


@pytest.mark.parametrize(
    "env_name",
    ["MCP_HTTP_ACCESS_TOKEN", "AWX_MCP_HTTP_ACCESS_TOKEN", "AWX_MCP_MCP_HTTP_ACCESS_TOKEN"],
)
def test_settings_mcp_http_access_token_env_aliases(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    """Test that legacy and prefix-generated HTTP auth token env vars work."""
    monkeypatch.setenv("AWX_HOST", "https://awx.example.com")
    monkeypatch.setenv("AWX_TOKEN", "test-token")
    monkeypatch.setenv(env_name, "http-token")

    settings = Settings()

    assert settings.mcp_http_access_token is not None
    assert settings.mcp_http_access_token.get_secret_value() == "http-token"


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
