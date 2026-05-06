"""Configuration management for NetBox MCP Server."""

import logging

from mcp_common.config import MCPSettings
from pydantic import AnyUrl, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict


class Settings(MCPSettings):
    """
    Centralized configuration for NetBox MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults

    Environment variables should match field names (e.g., NETBOX_URL, TRANSPORT).
    Inherits transport, host, port, mcp_http_access_token, debug, log_level,
    and log_json from MCPSettings.
    """

    netbox_url: AnyUrl
    netbox_token: SecretStr
    verify_ssl: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 < v < 65536):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("netbox_url")
    @classmethod
    def validate_netbox_url(cls, v: AnyUrl) -> AnyUrl:
        if not v.scheme or not v.host:
            raise ValueError(
                "NETBOX_URL must include scheme and host (e.g., https://netbox.example.com/)"
            )
        return v

    def get_effective_config_summary(self) -> dict[str, object]:
        """Return a non-secret summary of effective configuration for logging."""
        return {
            "netbox_url": str(self.netbox_url),
            "netbox_token": "***REDACTED***",
            "transport": self.transport,
            "host": self.host if self.transport == "http" else "N/A",
            "port": self.port if self.transport == "http" else "N/A",
            "mcp_http_access_token": "***REDACTED***"
            if (self.transport == "http" and self.mcp_http_access_token is not None)
            else "N/A",
            "verify_ssl": self.verify_ssl,
            "log_level": self.log_level,
        }


def suppress_noisy_loggers(log_level: str) -> None:
    """Keep urllib3/httpx/requests quiet unless we're at DEBUG."""
    target = "DEBUG" if log_level == "DEBUG" else "WARNING"
    for name in ("urllib3", "httpx", "requests"):
        logging.getLogger(name).setLevel(getattr(logging, target))
