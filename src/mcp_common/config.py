"""Base configuration for MCP servers using pydantic-settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    """Base settings class for MCP servers.

    Subclass this to define your server's configuration. Environment variables
    are loaded automatically with an optional prefix.

    Example::

        class MyServerSettings(MCPSettings):
            model_config = SettingsConfigDict(env_prefix="MY_SERVER_")
            api_url: str
            api_token: str
            timeout: int = 30
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    github_repo: str | None = Field(
        default=None,
        description="GitHub repository for agent issue workflow (format owner/name).",
    )
    issue_tracker_url: str | None = Field(
        default=None,
        description="Optional URL to the project issue tracker (e.g. non-GitHub).",
    )

    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    stateless_http: bool = True
    mcp_http_access_token: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_log_level(cls, data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data.get("log_level"), str):
            data["log_level"] = data["log_level"].upper()
        return data
