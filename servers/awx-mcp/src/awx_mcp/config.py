"""Configuration management for AWX MCP Server.

This module provides centralized configuration management using Pydantic Settings,
supporting multiple configuration sources with proper precedence.

Configuration Sources (in order of priority):
1. Command-line arguments
2. Environment variables (multiple aliases supported)
3. .env files (awx-mcp/.env or ../.env)
4. Default values

Environment Variables:
- AWX_HOST / CONTROLLER_HOST: AWX instance URL (optional; defaults to
  https://awx.internal.together.ai/)
- AWX_TOKEN / CONTROLLER_OAUTH_TOKEN: API authentication token
- API_BASE_PATH: API path (default: /api/v2)
- TRANSPORT: MCP transport (stdio/http, default: stdio)
- HOST: HTTP bind host (default: 127.0.0.1)
- PORT: HTTP bind port (default: 8000)
- MCP_HTTP_ACCESS_TOKEN: Required for HTTP transport authentication
- VERIFY_SSL: SSL certificate verification (default: true)
- TIMEOUT_SECONDS: HTTP client timeout (default: 30)
- LOG_LEVEL: Logging verbosity (default: INFO)

Credential references:
- AWX_TOKEN and MCP_HTTP_ACCESS_TOKEN may each be either a literal value or a
  1Password reference ``op://<vault>/<item>/<field>``. References are resolved
  at runtime through the shared ``mcp_common`` credential chain (``op`` CLI +
  kernel-keyring cache) via :func:`resolve_secret`; literal values pass through
  unchanged so existing configurations keep working exactly as before.

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

from mcp_common.credential_chain import (
    CachedResolver,
    CredentialChain,
    OnePasswordResolver,
)
from pydantic import AliasChoices, AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Default AWX/Controller base URL. ``AWX_HOST`` is therefore optional; set it
#: (literal or ``op://`` ref) to point at a different instance.
DEFAULT_AWX_HOST = "https://awx.internal.together.ai/"


class Settings(BaseSettings):
    """
    Centralized configuration for AWX MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults
    """

    # ===== Core AWX Settings =====
    awx_host: AnyUrl = Field(
        default=DEFAULT_AWX_HOST,  # validate_default coerces this str into an AnyUrl
        validate_default=True,
        validation_alias=AliasChoices("AWX_HOST", "CONTROLLER_HOST"),
    )
    """Base URL of the AWX/Controller instance (e.g., https://awx.example.com/).

    Optional: defaults to :data:`DEFAULT_AWX_HOST`. Override via ``AWX_HOST`` /
    ``CONTROLLER_HOST`` (a literal URL or an ``op://`` reference).
    """

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


def resolve_secret(raw: str, *, key_name: str) -> str:
    """Resolve a credential that may be a literal or a 1Password ``op://`` ref.

    Routes AWX credentials through the shared ``mcp_common`` credential chain so
    the same configuration value can be either:

    * a **literal token** — returned unchanged, so existing configs keep working
      exactly as they do today (no ``op`` call, no behaviour change); or
    * an **``op://Vault/Item/field`` reference** — resolved at runtime via the
      1Password CLI (``op read``) and cached in the Linux kernel keyring under
      *key_name* (mirrors netbox-mcp's ``CachedResolver`` usage) so concurrent
      processes avoid repeated biometric prompts.

    *raw* is whatever upstream precedence already selected — pydantic ``Settings``
    (which merges CLI args, the ``AWX_TOKEN`` / ``CONTROLLER_OAUTH_TOKEN`` aliases
    and ``.env``) in the server, or a direct environment read in the CLI — so
    callers keep their existing source-of-truth and only gain ``op://`` support.

    Args:
        raw: The literal value or ``op://`` reference. An empty string is
            returned unchanged (callers treat empty as "credential absent").
        key_name: Kernel-keyring cache key, e.g. ``"mcp:awx-token"``.

    Returns:
        The resolved secret value (or the empty string when *raw* is empty).

    Raises:
        RuntimeError: If an ``op://`` reference cannot be resolved.
        NotImplementedError: If *raw* is a ``vault://`` reference (reserved).
    """
    if not raw:
        return raw
    if raw.startswith("vault://"):
        raise NotImplementedError(
            "vault:// credential references are not yet supported. "
            "Use op:// or a plain credential value."
        )
    if not raw.startswith("op://"):
        return raw
    chain = CredentialChain(
        [CachedResolver(inner=OnePasswordResolver(raw), key_name=key_name)],
        name=key_name,
    )
    return chain.get()
