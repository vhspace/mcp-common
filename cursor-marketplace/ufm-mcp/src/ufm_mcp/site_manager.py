"""Multi-site UFM client management.

Subclasses ``mcp_common.SiteManager`` to handle UFM-specific env var
patterns (``UFM_<SITE>_TOKEN`` / ``UFM_<SITE>_ACCESS_TOKEN``), REST client
lifecycle, and mutable active-site selection.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastmcp.exceptions import ToolError
from mcp_common import SiteConfig as BaseSiteConfig
from mcp_common import SiteManager as BaseSiteManager
from pydantic import field_validator

from ufm_mcp.config import Settings
from ufm_mcp.ufm_client import UfmRestClient

logger = logging.getLogger(__name__)

_PATH_FIELDS = (
    "ufm_api_base_path",
    "ufm_resources_base_path",
    "ufm_logs_base_path",
    "ufm_web_base_path",
    "ufm_backup_base_path",
    "ufm_jobs_base_path",
)


class UfmSiteConfig(BaseSiteConfig):
    """Per-site configuration for a UFM instance."""

    url: str
    token: str | None = None
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    ufm_api_base_path: str = "/ufmRestV3"
    ufm_resources_base_path: str = "/ufmRestV3"
    ufm_logs_base_path: str = "/ufmRestV3"
    ufm_web_base_path: str = "/ufm_web"
    ufm_backup_base_path: str = "/ufmRestV3"
    ufm_jobs_base_path: str = "/ufmRestV3"

    @field_validator(*_PATH_FIELDS, mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if isinstance(v, str) else v

    # --- Backward-compatible aliases ---

    @property
    def ufm_url(self) -> str:
        return self.url

    @property
    def ufm_token(self) -> str | None:
        return self.token


class UfmSiteManager(BaseSiteManager["UfmSiteConfig"]):
    """Manages multi-site UFM connections.

    Sites are discovered from:
    1. The base Settings (becomes the "default" site)
    2. Environment variables like UFM_<SITE>_URL / UFM_<SITE>_TOKEN
    3. Optional alias mapping via UFM_SITE_ALIASES_JSON
    """

    env_prefix = "UFM"

    def __init__(self) -> None:
        super().__init__(UfmSiteConfig)
        self._clients: dict[str, UfmRestClient] = {}
        self._active_key: str | None = None

    @property
    def active_key(self) -> str | None:
        return self._active_key

    def configure(self, base: Settings) -> None:
        """Initialize sites from base settings + environment."""
        token = base.ufm_token.get_secret_value() if base.ufm_token else None
        default_name = _site_key(os.environ.get("UFM_DEFAULT_SITE", "default"))

        default_cfg = UfmSiteConfig(
            site=default_name,
            url=str(base.ufm_url),
            token=token,
            verify_ssl=base.verify_ssl,
            timeout_seconds=base.timeout_seconds,
            ufm_api_base_path=base.ufm_api_base_path,
            ufm_resources_base_path=base.ufm_resources_base_path,
            ufm_logs_base_path=base.ufm_logs_base_path,
            ufm_web_base_path=base.ufm_web_base_path,
            ufm_backup_base_path=base.ufm_backup_base_path,
            ufm_jobs_base_path=base.ufm_jobs_base_path,
        )
        self._register_site(default_cfg)
        self._register_alias("default", default_name)
        self._ensure_client(default_cfg)

        self._discover_env_sites(base)
        self._load_alias_json()

        self._active_key = self.resolve(default_name)

    def _discover_env_sites(self, base: Settings) -> None:
        """Auto-discover sites from UFM_<SITE>_URL environment variables.

        Handles both TOKEN and ACCESS_TOKEN env var patterns.
        """
        prefix = self.env_prefix.upper()
        url_pattern = re.compile(rf"^{re.escape(prefix)}_([A-Z0-9][A-Z0-9_]*)_URL$")

        for env_key, env_val in sorted(os.environ.items()):
            m = url_pattern.match(env_key)
            if not m:
                continue

            raw_site_name = m.group(1)
            site_k = _site_key(raw_site_name)
            if site_k in self._sites:
                continue

            site_token = os.environ.get(
                f"{prefix}_{raw_site_name}_TOKEN"
            ) or os.environ.get(f"{prefix}_{raw_site_name}_ACCESS_TOKEN")

            verify_raw = os.environ.get(f"{prefix}_{raw_site_name}_VERIFY_SSL", "").strip().lower()
            verify = (
                verify_raw in {"1", "true", "yes", "on"} if verify_raw else base.verify_ssl
            )

            timeout = float(
                os.environ.get(
                    f"{prefix}_{raw_site_name}_TIMEOUT_SECONDS",
                    str(base.timeout_seconds),
                )
            )

            cfg = UfmSiteConfig(
                site=site_k,
                url=env_val,
                token=site_token,
                verify_ssl=verify,
                timeout_seconds=timeout,
                ufm_api_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_API_BASE_PATH", base.ufm_api_base_path
                ),
                ufm_resources_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_RESOURCES_BASE_PATH",
                    base.ufm_resources_base_path,
                ),
                ufm_logs_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_LOGS_BASE_PATH", base.ufm_logs_base_path
                ),
                ufm_web_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_WEB_BASE_PATH", base.ufm_web_base_path
                ),
                ufm_backup_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_BACKUP_BASE_PATH", base.ufm_backup_base_path
                ),
                ufm_jobs_base_path=os.environ.get(
                    f"{prefix}_{raw_site_name}_JOBS_BASE_PATH", base.ufm_jobs_base_path
                ),
            )
            self._register_site(cfg)
            self._ensure_client(cfg)

    def _ensure_client(self, cfg: UfmSiteConfig) -> None:
        """Create a REST client for a site config if not already cached."""
        key = _site_key(cfg.site)
        if key not in self._clients:
            self._clients[key] = UfmRestClient(
                base_url=cfg.url,
                token=cfg.token,
                verify_ssl=cfg.verify_ssl,
                timeout_seconds=cfg.timeout_seconds,
            )

    def resolve(self, site: str | None) -> str:
        """Resolve a site key/alias to the canonical site key.

        Raises ToolError (not KeyError) for backward compatibility with
        the MCP tool layer.
        """
        if site is None or not site.strip():
            if self._active_key:
                return self._active_key
            raise ToolError("No active UFM site configured")

        try:
            return super().resolve(site)
        except KeyError as exc:
            raise ToolError(str(exc)) from None

    def set_active(self, site: str) -> UfmSiteConfig:
        """Explicitly set the active site. Returns the site config."""
        key = self.resolve(site)
        self._active_key = key
        return self._sites[key]

    def get_client(self, site: str | None = None) -> UfmRestClient:
        """Get the UfmRestClient for a site without changing active site."""
        key = self.resolve(site)
        return self._clients[key]

    def get_config(self, site: str | None = None) -> UfmSiteConfig:
        """Get the UfmSiteConfig for a site without changing active site."""
        key = self.resolve(site)
        return self._sites[key]

    def get_effective_summary(self) -> dict[str, Any]:
        """Return a summary suitable for ufm_get_config output."""
        cfg = self.get_config()
        return {
            "active_site": self._active_key,
            "sites": sorted(self._sites.keys()),
            "site_aliases": dict(sorted(self._aliases.items())),
            "ufm_url": cfg.url,
            "ufm_token": "***REDACTED***" if cfg.token else None,
            "verify_ssl": cfg.verify_ssl,
            "timeout_seconds": cfg.timeout_seconds,
            "ufm_api_base_path": cfg.ufm_api_base_path,
            "ufm_resources_base_path": cfg.ufm_resources_base_path,
            "ufm_logs_base_path": cfg.ufm_logs_base_path,
            "ufm_web_base_path": cfg.ufm_web_base_path,
            "ufm_backup_base_path": cfg.ufm_backup_base_path,
            "ufm_jobs_base_path": cfg.ufm_jobs_base_path,
        }

    def list_sites(self) -> list[dict[str, Any]]:  # type: ignore[override]
        """Return site info suitable for ufm_list_sites output."""
        return [
            {
                "site": key,
                "ufm_url": cfg.url,
                "verify_ssl": cfg.verify_ssl,
                "timeout_seconds": cfg.timeout_seconds,
                "active": key == self._active_key,
            }
            for key, cfg in sorted(self._sites.items())
        ]

    def close_all(self) -> None:
        """Close all REST clients."""
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass


# Backward-compatible alias so existing imports still work.
SiteManager = UfmSiteManager
SiteConfig = UfmSiteConfig


def _site_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
