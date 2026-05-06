"""Configuration for gpu-diag-mcp server."""

from __future__ import annotations

from mcp_common import MCPSettings
from pydantic_settings import SettingsConfigDict


class Settings(MCPSettings):
    """Server settings loaded from environment / ``.env`` file.

    Subclasses ``mcp_common.MCPSettings`` which provides:
      - ``debug``, ``log_level``, ``log_json``
      - ``transport`` (``"stdio"`` | ``"http"``), ``host``, ``port``
      - ``mcp_http_access_token`` (optional Bearer/X-API-Key auth)
    """

    model_config = SettingsConfigDict(env_prefix="GPU_DIAG_")
