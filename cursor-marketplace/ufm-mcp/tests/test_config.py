from __future__ import annotations

import pytest

from ufm_mcp.config import Settings


def test_settings_redacts_secret_fields() -> None:
    settings = Settings(
        ufm_url="https://ufm.example.com/",
        ufm_token="secret-token",
        transport="stdio",
        verify_ssl=False,
        timeout_seconds=15,
    )

    summary = settings.get_effective_config_summary()
    assert summary["ufm_token"] == "***REDACTED***"
    assert summary["verify_ssl"] is False
    assert summary["transport"] == "stdio"


def test_settings_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="TIMEOUT_SECONDS must be > 0"):
        Settings(ufm_url="https://ufm.example.com/", timeout_seconds=0)


def test_settings_rejects_invalid_port() -> None:
    with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
        Settings(ufm_url="https://ufm.example.com/", transport="http", port=70000)
