"""Configuration management for AWX MCP Server.

This module provides centralized configuration management using Pydantic Settings,
supporting multiple configuration sources with proper precedence.

Configuration Sources (in order of priority):
1. Command-line arguments
2. Environment variables (multiple aliases supported)
3. .env files (awx-mcp/.env or ../.env)
4. Default values

Environment Variables:
- AWX_HOST / CONTROLLER_HOST: AWX instance URL
- AWX_TOKEN / CONTROLLER_OAUTH_TOKEN: API authentication token
- API_BASE_PATH: API path (default: /api/v2)
- TRANSPORT: MCP transport (stdio/http, default: stdio)
- HOST: HTTP bind host (default: 127.0.0.1)
- PORT: HTTP bind port (default: 8000)
- MCP_HTTP_ACCESS_TOKEN: Required for HTTP transport authentication
- VERIFY_SSL: SSL certificate verification (default: true)
- TIMEOUT_SECONDS: HTTP client timeout (default: 30)
- LOG_LEVEL: Logging verbosity (default: INFO)

Security:
- Authentication tokens are treated as secrets and redacted in logs
- HTTP access tokens are required for HTTP transport
- SSL verification defaults to enabled

Validation:
- Host URLs must be valid HTTP/HTTPS URLs
- Ports must be in valid range (1-65535)
- Timeouts must be positive
- API paths must start with '/'
"""

from typing import Any, Literal

from pydantic import AliasChoices, AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized configuration for AWX MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults
    """

    # ===== Core AWX Settings =====
    awx_host: AnyUrl = Field(
        validation_alias=AliasChoices("AWX_HOST", "CONTROLLER_HOST"),
    )
    """Base URL of the AWX/Controller instance (e.g., https://awx.example.com/)"""

    awx_token: SecretStr = Field(
        validation_alias=AliasChoices("AWX_TOKEN", "CONTROLLER_OAUTH_TOKEN"),
    )
    """OAuth2 Personal Access Token (treated as secret)"""

    api_base_path: str = "/api/v2"
    """API base path. Defaults to /api/v2 for AWX/Controller."""

    # ===== Transport Settings =====
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000

    # ===== HTTP Transport Auth (Server-side) =====
    #
    # If you run the MCP server in HTTP mode, you should require a separate access token
    # to guard the tool surface area. This is distinct from the AWX token.
    mcp_http_access_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MCP_HTTP_ACCESS_TOKEN", "AWX_MCP_HTTP_ACCESS_TOKEN"),
    )

    # ===== HTTP Client Settings =====
    verify_ssl: bool = True
    timeout_seconds: float = 30.0

    # ===== Observability Settings =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    model_config = SettingsConfigDict(
        # Allow either:
        # - awx-mcp/.env (recommended when running with --directory awx-mcp)
        # - repo-root/.env (common when secrets are centralized)
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
            "awx_host": str(self.awx_host),
            "awx_token": "***REDACTED***",
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
