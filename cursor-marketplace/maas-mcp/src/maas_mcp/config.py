"""Configuration management for MAAS MCP Server.

This module provides centralized configuration management using Pydantic Settings,
supporting multiple configuration sources with proper precedence.

Configuration Sources (in order of priority):
1. Command-line arguments
2. Environment variables (multiple aliases supported)
3. .env files (maas-mcp/.env or ../.env)
4. Default values

Environment Variables:
- MAAS_URL / MAAS_HOST: MAAS instance URL (single instance mode)
- MAAS_API_KEY: API key (format: consumer_key:consumer_token:secret)
- MAAS_INSTANCES: JSON dict of named MAAS instances (multi-instance mode)
- NETBOX_URL: NetBox instance URL (optional, for cross-referencing)
- NETBOX_TOKEN: NetBox API token (optional)
- NETBOX_MCP_SERVER: NetBox MCP server name (optional, alternative to direct API)
- TRANSPORT: MCP transport (stdio/http, default: stdio)
- HOST: HTTP bind host (default: 127.0.0.1)
- PORT: HTTP bind port (default: 8000)
- MCP_HTTP_ACCESS_TOKEN: Required for HTTP transport authentication
- VERIFY_SSL: SSL certificate verification (default: true)
- TIMEOUT_SECONDS: HTTP client timeout (default: 30)
- LOG_LEVEL: Logging verbosity (default: INFO)

Security:
- API keys are treated as secrets and redacted in logs
- HTTP access tokens are required for HTTP transport
- SSL verification defaults to enabled

Validation:
- Host URLs must be valid HTTP/HTTPS URLs
- Ports must be in valid range (1-65535)
- Timeouts must be positive
- API keys must be in correct format (key:token:secret)
"""

import json
import logging
import logging.config
import os
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    AnyUrl,
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from maas_mcp.site_manager import MaasSiteManager, _ensure_scheme  # noqa: F401 (re-exported)


def _discover_prefixed_instances(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Auto-discover MAAS instances via MaasSiteManager.

    This is a compatibility wrapper. When ``env`` is None (the common case),
    it delegates entirely to ``MaasSiteManager.configure()``. The ``env``
    parameter is retained for testing but ignored by the new implementation
    (env vars must be in os.environ).
    """
    mgr = MaasSiteManager()
    mgr.configure()
    return mgr.get_instances_dict()


class MaasInstanceConfig(BaseModel):
    """Configuration for a single MAAS instance."""

    url: AnyUrl
    api_key: SecretStr


class Settings(BaseSettings):
    """Centralized configuration for MAAS MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults

    Instance discovery (in order, all sources merged):
    1. MAAS_URL/MAAS_API_KEY → registered as "default"
    2. MAAS_INSTANCES JSON → registered by name
    3. MAAS_{SITE}_URL + MAAS_{SITE}_API[_KEY] → registered as lowercase site name

    Use MAAS_DEFAULT_SITE to set which instance is aliased as "default".
    Use MAAS_SITE_ALIASES_JSON to add short aliases (e.g. {"prod": "central"}).
    """

    # ===== Core MAAS Settings =====
    maas_url: AnyUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("MAAS_URL", "MAAS_HOST"),
    )
    """Base URL of the MAAS instance (single-instance mode)"""

    maas_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="MAAS_API_KEY",
    )
    """API key in format: consumer_key:consumer_token:secret (single-instance mode)"""

    maas_instances: dict[str, dict[str, str]] | None = Field(
        default=None,
        validation_alias="MAAS_INSTANCES",
    )
    """JSON dict of named MAAS instances (multi-instance mode).
    Format: {"name": {"url": "...", "api_key": "..."}}
    """

    maas_default_site: str | None = Field(
        default=None,
        validation_alias="MAAS_DEFAULT_SITE",
    )
    """Name of the site to alias as 'default' (e.g. 'central')."""

    maas_site_aliases: dict[str, str] | None = Field(
        default=None,
        validation_alias="MAAS_SITE_ALIASES_JSON",
    )
    """JSON map of alias → canonical site name (e.g. {"prod": "central"})."""

    # ===== NetBox Integration Settings =====
    netbox_url: AnyUrl | None = Field(default=None, validation_alias="NETBOX_URL")
    """Base URL of NetBox instance (optional, for cross-referencing)"""

    netbox_token: SecretStr | None = Field(default=None, validation_alias="NETBOX_TOKEN")
    """NetBox API token (optional)"""

    netbox_mcp_server: str | None = Field(default=None, validation_alias="NETBOX_MCP_SERVER")
    """NetBox MCP server name (optional, alternative to direct API)"""

    # ===== MAAS Database Settings (for hardware_info import) =====
    maas_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAAS_DB_URL", "MAAS_DATABASE_URL"),
    )
    """PostgreSQL connection string for direct DB access (e.g. postgresql://maas:pass@host/maasdb).
    Only needed for sync_hardware_info in migrate-node. Per-instance overrides:
    MAAS_{SITE}_DB_URL (e.g. MAAS_CENTRAL_DB_URL)."""

    # ===== Transport Settings =====
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    stateless_http: bool = False

    # ===== HTTP Transport Auth (Server-side) =====
    mcp_http_access_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MCP_HTTP_ACCESS_TOKEN", "MAAS_MCP_HTTP_ACCESS_TOKEN"),
    )

    # ===== HTTP Client Settings =====
    verify_ssl: bool = True
    timeout_seconds: float = 30.0

    # ===== Observability Settings =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("maas_api_key", mode="before")
    @classmethod
    def validate_api_key_format(cls, v: str | None) -> str | None:
        """Validate API key format (key:token:secret)."""
        if v is None:
            return None
        parts = v.split(":")
        if len(parts) != 3:
            raise ValueError("MAAS_API_KEY must be in format: consumer_key:consumer_token:secret")
        return v

    @field_validator("maas_instances", mode="before")
    @classmethod
    def parse_maas_instances(
        cls, v: str | dict[str, Any] | None
    ) -> dict[str, dict[str, str]] | None:
        """Parse MAAS_INSTANCES from JSON string or dict."""
        if v is None:
            return None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"MAAS_INSTANCES must be valid JSON: {e}") from e
        if not isinstance(v, dict):
            raise ValueError("MAAS_INSTANCES must be a dict")
        for name, config in v.items():
            if not isinstance(config, dict):
                raise ValueError(f"MAAS instance '{name}' must be a dict")
            if "url" not in config or "api_key" not in config:
                raise ValueError(f"MAAS instance '{name}' must have 'url' and 'api_key'")
            parts = config["api_key"].split(":")
            if len(parts) != 3:
                raise ValueError(
                    f"MAAS instance '{name}' API key must be in format: consumer_key:consumer_token:secret"
                )
        return v

    @field_validator("maas_site_aliases", mode="before")
    @classmethod
    def parse_site_aliases(cls, v: str | dict[str, Any] | None) -> dict[str, str] | None:
        """Parse MAAS_SITE_ALIASES_JSON from JSON string or dict."""
        if v is None:
            return None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"MAAS_SITE_ALIASES_JSON must be valid JSON: {e}") from e
        if not isinstance(v, dict):
            raise ValueError("MAAS_SITE_ALIASES_JSON must be a dict")
        return {str(k).lower(): str(val).lower() for k, val in v.items()}

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

    @model_validator(mode="after")
    def validate_maas_config(self) -> "Settings":
        """Ensure at least one MAAS instance can be resolved.

        Avoids constructing a full MaasSiteManager just for validation —
        checks pydantic-parsed fields first, then does a lightweight env
        scan only if needed.
        """
        has_single = self.maas_url is not None and self.maas_api_key is not None
        has_multi = self.maas_instances is not None and len(self.maas_instances) > 0

        if not (has_single or has_multi):
            mgr = MaasSiteManager()
            mgr.configure()
            if not mgr.has_sites():
                raise ValueError(
                    "No MAAS instances configured. Provide one of:\n"
                    "  - MAAS_URL + MAAS_API_KEY (single instance)\n"
                    "  - MAAS_INSTANCES JSON (multi-instance)\n"
                    "  - MAAS_{SITE}_URL + MAAS_{SITE}_API_KEY env vars (per-site)"
                )

        return self

    def get_maas_instances(self) -> dict[str, MaasInstanceConfig]:
        """Get all configured MAAS instances via MaasSiteManager.

        Delegates discovery to MaasSiteManager.configure() which merges all
        three sources (prefixed env, JSON, single-instance) and handles
        aliases and default site promotion.
        """
        mgr = MaasSiteManager()
        mgr.configure()

        instances: dict[str, MaasInstanceConfig] = {}
        for name, cfg in mgr.sites.items():
            instances[name] = MaasInstanceConfig(
                url=AnyUrl(cfg.url),
                api_key=SecretStr(cfg.api_key),
            )

        # Also register alias targets so "default" resolves
        if mgr.default_site and mgr.default_site in instances and "default" not in instances:
            instances["default"] = instances[mgr.default_site]

        for alias, target in mgr.aliases.items():
            if target in instances and alias not in instances:
                instances[alias] = instances[target]

        return instances

    def get_effective_config_summary(self) -> dict[str, Any]:
        """Return a non-secret summary of effective configuration for logging."""
        instances_summary = {}
        for name, config in self.get_maas_instances().items():
            instances_summary[name] = {
                "url": str(config.url),
                "api_key": "***REDACTED***",
            }

        return {
            "maas_instances": instances_summary,
            "netbox_url": str(self.netbox_url) if self.netbox_url else None,
            "netbox_token": "***REDACTED***" if self.netbox_token else None,
            "netbox_mcp_server": self.netbox_mcp_server,
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


def configure_logging(
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
) -> None:
    """Configure structured logging."""
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "httpx": {"level": "WARNING" if log_level != "DEBUG" else "DEBUG"},
            "requests": {"level": "WARNING" if log_level != "DEBUG" else "DEBUG"},
            "requests_oauthlib": {"level": "WARNING" if log_level != "DEBUG" else "DEBUG"},
        },
        "root": {"level": log_level, "handlers": ["console"]},
    }
    logging.config.dictConfig(config)
