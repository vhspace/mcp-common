"""Base configuration for MCP servers using pydantic-settings."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, PrivateAttr, SecretStr, model_validator
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

    # Unified MCP / CLI logging (issue #17) — defaults preserve prior behavior.
    log_access: bool = Field(
        default=True,
        description="Emit access-channel logs when using stdio helpers or manual log_access_event(enabled=...).",
    )
    log_transcript: bool = Field(
        default=False,
        description="When True, transcript logs may be emitted (subject to sampling). Off by default.",
    )
    log_transcript_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Probability [0,1] that a given transcript log is emitted when log_transcript is True.",
    )
    log_transcript_max_str_len: int = Field(
        default=2048,
        ge=32,
        description="Max string length per field before ellipsis in transcript payloads.",
    )
    log_transcript_max_total_chars: int = Field(
        default=65536,
        ge=256,
        description="Max JSON-serialized size per payload; larger payloads collapse to a truncation marker.",
    )
    log_redact_key_substrings: list[str] = Field(
        default_factory=list,
        description="Extra key substrings (case-insensitive) to redact in transcript payloads.",
    )
    log_redact_key_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns matched against dict keys for redaction (e.g. '.*_SECRET$').",
    )
    log_trace_on_error: bool = Field(
        default=True,
        description="When False, mcp_log_trace is a no-op.",
    )
    log_trace_include_stack: bool = Field(
        default=False,
        description="Attach current stack_info to trace logs (expensive; off by default).",
    )
    log_request_id_header: str = Field(
        default="x-request-id",
        description="HTTP header to read/propagate for request correlation (lowercase).",
    )
    log_http_access: bool = Field(
        default=False,
        description="Opt-in: enable HTTP access middleware in create_http_app when wired with settings.",
    )
    _compiled_log_redact_key_patterns: tuple[re.Pattern[str], ...] = PrivateAttr(
        default_factory=tuple
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_logging_inputs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if isinstance(data.get("log_level"), str):
            data["log_level"] = data["log_level"].upper()
        if isinstance(data.get("log_request_id_header"), str):
            data["log_request_id_header"] = data["log_request_id_header"].strip().lower()
        return data

    @model_validator(mode="after")
    def _compile_log_redact_key_patterns(self) -> MCPSettings:
        compiled: list[re.Pattern[str]] = []
        for idx, pattern in enumerate(self.log_redact_key_patterns):
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                msg = f"Invalid log_redact_key_patterns[{idx}] regex {pattern!r}: {exc}"
                raise ValueError(msg) from exc
        self._compiled_log_redact_key_patterns = tuple(compiled)
        return self

    def compiled_log_redact_key_patterns(self) -> tuple[re.Pattern[str], ...]:
        """Return redaction key regexes compiled and validated at settings initialization."""
        return self._compiled_log_redact_key_patterns
