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
import re
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
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

_PREFIXED_URL_RE = re.compile(r"^MAAS_(.+?)_URL$", re.IGNORECASE)
_API_KEY_TEMPLATES = ("MAAS_{}_API_KEY", "MAAS_{}_API")
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _ensure_scheme(url: str) -> str:
    """Prepend http:// if the URL has no scheme so AnyUrl can parse it."""
    url = url.strip()
    if not _SCHEME_RE.match(url):
        url = f"http://{url}"
    return url


_ENV_FILE_PATHS = (".env", "../.env")


def _load_env_with_dotfiles() -> dict[str, str]:
    """Build a merged env dict: .env file values overlaid by real os.environ."""
    merged: dict[str, str] = {}
    for path in _ENV_FILE_PATHS:
        p = Path(path)
        if p.is_file():
            for k, v in dotenv_values(p).items():
                if v is not None:
                    merged[k] = v
    merged.update(os.environ)
    return merged


def _discover_prefixed_instances(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Auto-discover MAAS instances from MAAS_{SITE}_URL + MAAS_{SITE}_API[_KEY] env vars.

    Scans both .env files and os.environ for pairs like:
        MAAS_ORI_URL + MAAS_ORI_API_KEY   (or MAAS_ORI_API)
        MAAS_CENTRAL_URL + MAAS_CENTRAL_API_KEY   (or MAAS_CENTRAL_API)

    Returns a dict of {site_name_lower: {"url": ..., "api_key": ...}}.
    """
    source = env if env is not None else _load_env_with_dotfiles()
    instances: dict[str, dict[str, str]] = {}

    for key in list(source):
        m = _PREFIXED_URL_RE.match(key)
        if not m:
            continue
        site = m.group(1)
        url = source[key]
        if not url:
            continue

        api_key: str | None = None
        for template in _API_KEY_TEMPLATES:
            candidate = template.format(site)
            for env_key in (candidate, candidate.upper()):
                val = source.get(env_key)
                if val:
                    api_key = val
                    break
            if api_key:
                break

        if api_key:
            instances[site.lower()] = {"url": url, "api_key": api_key}

    return instances


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
        """Ensure at least one MAAS instance can be resolved."""
        has_single = self.maas_url is not None and self.maas_api_key is not None
        has_multi = self.maas_instances is not None and len(self.maas_instances) > 0
        has_prefixed = bool(_discover_prefixed_instances())

        if not (has_single or has_multi or has_prefixed):
            raise ValueError(
                "No MAAS instances configured. Provide one of:\n"
                "  - MAAS_URL + MAAS_API_KEY (single instance)\n"
                "  - MAAS_INSTANCES JSON (multi-instance)\n"
                "  - MAAS_{SITE}_URL + MAAS_{SITE}_API_KEY env vars (per-site)"
            )

        return self

    def get_maas_instances(self) -> dict[str, MaasInstanceConfig]:
        """Get all configured MAAS instances, merging all discovery sources.

        Sources (later sources override earlier for the same name):
        1. {SITE}_MAAS_URL env var auto-discovery
        2. MAAS_INSTANCES JSON
        3. MAAS_URL/MAAS_API_KEY as "default"

        After merging, MAAS_DEFAULT_SITE is aliased to "default" (if not
        already present) and MAAS_SITE_ALIASES_JSON entries are added.
        """
        instances: dict[str, MaasInstanceConfig] = {}

        for name, config in _discover_prefixed_instances().items():
            instances[name] = MaasInstanceConfig(
                url=AnyUrl(_ensure_scheme(config["url"])),
                api_key=SecretStr(config["api_key"]),
            )

        if self.maas_instances:
            for name, config in self.maas_instances.items():
                instances[name] = MaasInstanceConfig(
                    url=AnyUrl(_ensure_scheme(config["url"])),
                    api_key=SecretStr(config["api_key"]),
                )

        if self.maas_url and self.maas_api_key:
            instances["default"] = MaasInstanceConfig(
                url=AnyUrl(str(self.maas_url)),
                api_key=SecretStr(self.maas_api_key.get_secret_value()),
            )

        if self.maas_default_site:
            site = self.maas_default_site.lower()
            if site in instances and "default" not in instances:
                instances["default"] = instances[site]

        if self.maas_site_aliases:
            for alias, canonical in self.maas_site_aliases.items():
                if canonical in instances and alias not in instances:
                    instances[alias] = instances[canonical]

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
