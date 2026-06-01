"""Tests for configuration management."""

import pytest
from pydantic import ValidationError

from weka_mcp.config import Settings


def test_settings_defaults(monkeypatch):
    """Test that settings have reasonable defaults when explicitly set."""
    monkeypatch.delenv("WEKA_USERNAME", raising=False)
    monkeypatch.delenv("WEKA_USER", raising=False)
    settings = Settings(
        weka_host="https://weka01:14000",
        weka_password="test-password",
        verify_ssl=True,
    )
    assert str(settings.weka_host) == "https://weka01:14000/"
    assert settings.weka_username == "admin"
    assert settings.api_base_path == "/api/v2"
    assert settings.transport == "stdio"
    assert settings.verify_ssl is True
    assert settings.timeout_seconds == 30.0


def test_settings_validation():
    """Test that settings validate correctly."""
    settings = Settings(
        weka_host="https://weka01:14000",
        weka_password="test-password",
        api_base_path="/api/v2",
    )
    assert settings.api_base_path == "/api/v2"

    with pytest.raises(ValidationError):
        Settings(
            weka_host="https://weka01:14000",
            weka_password="test-password",
            api_base_path="api/v2",
        )

    with pytest.raises(ValidationError):
        Settings(
            weka_host="https://weka01:14000",
            weka_password="test-password",
            port=70000,
        )


def test_settings_timeout_validation():
    """Test that negative timeout is rejected."""
    with pytest.raises(ValidationError):
        Settings(
            weka_host="https://weka01:14000",
            weka_password="test-password",
            timeout_seconds=-1,
        )


def test_effective_config_summary_redacts_secrets(monkeypatch):
    """Test that the summary redacts sensitive values."""
    monkeypatch.delenv("VERIFY_SSL", raising=False)
    settings = Settings(
        weka_host="https://weka01:14000",
        weka_password="super-secret",
    )
    summary = settings.get_effective_config_summary()
    assert summary["weka_password"] == "***REDACTED***"
    assert "super-secret" not in str(summary)


def test_settings_optional_base_credentials(monkeypatch):
    """Settings should instantiate without WEKA_HOST/WEKA_PASSWORD when only
    site-specific env vars are present."""
    monkeypatch.delenv("WEKA_HOST", raising=False)
    monkeypatch.delenv("WEKA_PASSWORD", raising=False)
    monkeypatch.delenv("WEKA_CLUSTER_HOST", raising=False)
    monkeypatch.delenv("WEKA_PASS", raising=False)
    settings = Settings()
    assert settings.weka_host is None
    assert settings.weka_password is None
    assert settings.weka_username == "admin"


def test_effective_config_summary_with_none_credentials(monkeypatch):
    """Summary should handle None host/password gracefully."""
    monkeypatch.delenv("WEKA_HOST", raising=False)
    monkeypatch.delenv("WEKA_PASSWORD", raising=False)
    monkeypatch.delenv("WEKA_CLUSTER_HOST", raising=False)
    monkeypatch.delenv("WEKA_PASS", raising=False)
    settings = Settings()
    summary = settings.get_effective_config_summary()
    assert summary["weka_host"] is None
    assert summary["weka_password"] is None
