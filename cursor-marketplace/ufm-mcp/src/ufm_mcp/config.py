"""Configuration management for ufm-mcp.

This MCP server wraps NVIDIA UFM (Unified Fabric Manager) REST APIs.

Configuration sources (highest to lowest priority):
- Command-line args
- Environment variables
- .env files (ufm-mcp/.env or ../.env)
- Defaults
"""

from __future__ import annotations

import json as _json
from typing import Any

from mcp_common import MCPSettings
from pydantic import (
    AliasChoices,
    AnyUrl,
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import SettingsConfigDict

_DEFAULT_TOPAZ_AZ_MAP: dict[str, str] = {
    "ori": "us-south-2a",
    "5c_oh1": "us-central-8a",
}


class Settings(MCPSettings):
    # ===== UFM connection =====
    ufm_url: AnyUrl = Field(
        validation_alias=AliasChoices("UFM_URL", "UFM_HOST", "UFM_BASE_URL"),
    )
    """Base URL of UFM (e.g., https://ufm.example.com/ or https://172.19.2.60/)"""

    ufm_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("UFM_TOKEN", "UFM_ACCESS_TOKEN"),
    )
    """UFM access token for token-based auth (preferred)."""

    ufm_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("UFM_USERNAME", "UFM_USER"),
    )
    ufm_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("UFM_PASSWORD", "UFM_PASS"),
    )

    # ===== HTTP client behavior =====
    verify_ssl: bool = Field(
        default=True,
        validation_alias=AliasChoices("VERIFY_SSL", "UFM_VERIFY_SSL"),
    )
    timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("TIMEOUT_SECONDS", "UFM_TIMEOUT_SECONDS"),
    )

    # ===== UFM REST API base path =====
    #
    # NVIDIA docs distinguish:
    # - basic auth: /ufmRest
    # - client cert: /ufmRestV2
    # - token auth: /ufmRestV3
    ufm_api_base_path: str = Field(
        default="/ufmRestV3",
        validation_alias=AliasChoices("UFM_API_BASE_PATH", "API_BASE_PATH"),
    )

    # ===== UFM "resources" API base path =====
    #
    # Example (docs):
    # - GET /ufmRest/resources/ports?high_ber_only=true
    #
    # In token-auth deployments, this is commonly exposed under /ufmRestV3.
    ufm_resources_base_path: str = Field(
        default="/ufmRestV3",
        validation_alias=AliasChoices("UFM_RESOURCES_BASE_PATH", "RESOURCES_BASE_PATH"),
    )

    # ===== UFM Logging REST API base path =====
    #
    # NVIDIA's "Logging REST API" docs are typically documented under /ufmRest/app/logs.
    # In practice, some deployments may also expose logs under /ufmRestV3.
    # We keep this separate from ufm_api_base_path so version/events/alarms can stay
    # on /ufmRestV3 while logs can use /ufmRest if required.
    ufm_logs_base_path: str = Field(
        default="/ufmRestV3",
        validation_alias=AliasChoices("UFM_LOGS_BASE_PATH", "LOGS_BASE_PATH"),
    )

    # File download base for history jobs (docs: GET /ufm_web/<file_name>)
    ufm_web_base_path: str = Field(
        default="/ufm_web",
        validation_alias=AliasChoices("UFM_WEB_BASE_PATH", "WEB_BASE_PATH"),
    )

    # UFM "system dump" / backup endpoint base path.
    # NVIDIA REST API guide documents: POST /ufmRest/app/backup?mode=Default|Snapshot
    ufm_backup_base_path: str = Field(
        default="/ufmRestV3",
        validation_alias=AliasChoices("UFM_BACKUP_BASE_PATH", "BACKUP_BASE_PATH"),
    )

    # Job status endpoint base path used by backup redirect target.
    # Docs show redirects to: /ufmRestV2/jobs/<id> but token-auth deployments
    # may return /ufmRestV3/jobs/<id>.
    ufm_jobs_base_path: str = Field(
        default="/ufmRestV3",
        validation_alias=AliasChoices("UFM_JOBS_BASE_PATH", "JOBS_BASE_PATH"),
    )

    # ===== Topaz fabric health service =====
    topaz_endpoint: str = Field(
        default="localhost:50051",
        validation_alias=AliasChoices("TOPAZ_ENDPOINT", "TOPAZ_GRPC_ENDPOINT"),
    )
    """gRPC endpoint for the Topaz fabric health service."""

    topaz_az_map_json: str = Field(
        default="{}",
        validation_alias=AliasChoices("TOPAZ_AZ_MAP_JSON", "TOPAZ_AZ_MAP"),
    )
    """JSON mapping of site names to Topaz AZ identifiers."""

    @property
    def topaz_az_map(self) -> dict[str, str]:
        """Parse topaz_az_map_json and merge with defaults."""
        merged = dict(_DEFAULT_TOPAZ_AZ_MAP)
        try:
            overrides = _json.loads(self.topaz_az_map_json)
            if isinstance(overrides, dict):
                merged.update(overrides)
        except (ValueError, TypeError):
            pass
        return merged

    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("TIMEOUT_SECONDS must be > 0")
        return v

    @field_validator("port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (0 < v < 65536):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    def get_effective_config_summary(self) -> dict[str, Any]:
        return {
            "ufm_url": str(self.ufm_url),
            "ufm_token": "***REDACTED***" if self.ufm_token is not None else None,
            "ufm_username": self.ufm_username,
            "ufm_password": "***REDACTED***" if self.ufm_password is not None else None,
            "verify_ssl": self.verify_ssl,
            "timeout_seconds": self.timeout_seconds,
            "ufm_api_base_path": self.ufm_api_base_path,
            "ufm_resources_base_path": self.ufm_resources_base_path,
            "ufm_logs_base_path": self.ufm_logs_base_path,
            "ufm_web_base_path": self.ufm_web_base_path,
            "ufm_backup_base_path": self.ufm_backup_base_path,
            "ufm_jobs_base_path": self.ufm_jobs_base_path,
            "transport": self.transport,
            "host": self.host if self.transport == "http" else "N/A",
            "port": self.port if self.transport == "http" else "N/A",
            "mcp_http_access_token": "***REDACTED***"
            if (self.transport == "http" and self.mcp_http_access_token is not None)
            else "N/A",
            "log_level": self.log_level,
        }
