"""Configuration management for Weka MCP Server."""

import logging
from typing import Any

from mcp_common.config import MCPSettings
from pydantic import AliasChoices, AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict


class Settings(MCPSettings):
    """
    Centralized configuration for Weka MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults

    Inherits transport, host, port, mcp_http_access_token, debug, log_level,
    and log_json from MCPSettings.
    """

    weka_host: AnyUrl = Field(
        validation_alias=AliasChoices("WEKA_HOST", "WEKA_CLUSTER_HOST"),
    )

    weka_username: str = Field(
        validation_alias=AliasChoices("WEKA_USERNAME", "WEKA_USER"),
        default="admin",
    )

    weka_password: SecretStr = Field(
        validation_alias=AliasChoices("WEKA_PASSWORD", "WEKA_PASS"),
    )

    weka_org: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WEKA_ORG", "WEKA_ORGANIZATION"),
    )

    api_base_path: str = "/api/v2"
    verify_ssl: bool = True
    timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @field_validator("api_base_path")
    @classmethod
    def validate_api_base_path(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("/"):
            raise ValueError("API_BASE_PATH must start with '/' (e.g., /api/v2)")
        return v.rstrip("/")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 < v < 65536):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("TIMEOUT_SECONDS must be > 0")
        return v

    def get_effective_config_summary(self) -> dict[str, Any]:
        return {
            "weka_host": str(self.weka_host),
            "weka_username": self.weka_username,
            "weka_password": "***REDACTED***",
            "api_base_path": self.api_base_path,
            "transport": self.transport,
            "host": self.host if self.transport == "http" else "N/A",
            "port": self.port if self.transport == "http" else "N/A",
            "mcp_http_access_token": "***REDACTED***"
            if (self.transport == "http" and self.mcp_http_access_token is not None)
            else "N/A",
            "verify_ssl": self.verify_ssl,
            "timeout_seconds": self.timeout_seconds,
            "log_level": self.log_level,
        }


def suppress_noisy_loggers(log_level: str) -> None:
    """Keep httpx/urllib3 quiet unless we're at DEBUG."""
    target = "DEBUG" if log_level == "DEBUG" else "WARNING"
    for name in ("urllib3", "httpx", "requests"):
        logging.getLogger(name).setLevel(getattr(logging, target))
