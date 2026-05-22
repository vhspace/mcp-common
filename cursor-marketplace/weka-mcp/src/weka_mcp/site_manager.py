"""Multi-site Weka client management.

Discovers and manages connections to multiple Weka clusters from a single
MCP server instance, built on mcp_common.SiteManager.

Sites are discovered from:
1. The base Settings (becomes the "default" site)
2. Environment variables like WEKA_<SITE>_URL / WEKA_<SITE>_ADMIN
3. Optional alias mapping via WEKA_SITE_ALIASES_JSON
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastmcp.exceptions import ToolError
from mcp_common import SiteConfig, SiteManager
from pydantic import ConfigDict

from weka_mcp.config import Settings
from weka_mcp.weka_client import WekaRestClient

logger = logging.getLogger(__name__)


class WekaSiteConfig(SiteConfig):
    """Per-site Weka connection configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    weka_host: str
    username: str
    password: str
    org: str | None = None
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    api_base_path: str = "/api/v2"


class WekaSiteManager(SiteManager[WekaSiteConfig]):
    """Manages multi-site Weka connections.

    Extends mcp_common.SiteManager with Weka-specific env var aliases
    (ADMIN/USERNAME, ADMIN_PASSWORD/PASSWORD), URL /ui stripping,
    password-required enforcement, and lazy WekaRestClient lifecycle.
    """

    env_prefix = "WEKA"

    def __init__(self) -> None:
        super().__init__(WekaSiteConfig)
        self._clients: dict[str, WekaRestClient] = {}
        self._active_key: str | None = None

    @property
    def active_key(self) -> str | None:
        return self._active_key

    def configure(self, base: Settings) -> None:
        """Initialize sites from base settings + environment.

        If base.weka_host and base.weka_password are set, a "default" site is
        registered from them.  Otherwise only env-discovered sites are used.
        Raises ToolError if no sites end up configured at all.
        """
        default_name = _site_key(os.environ.get("WEKA_DEFAULT_SITE", "default"))
        has_base = base.weka_host is not None and base.weka_password is not None

        if has_base:
            default_cfg = WekaSiteConfig(
                site=default_name,
                weka_host=str(base.weka_host),
                username=base.weka_username,
                password=base.weka_password.get_secret_value(),
                org=base.weka_org,
                verify_ssl=base.verify_ssl,
                timeout_seconds=base.timeout_seconds,
                api_base_path=base.api_base_path,
            )
            self.register_site(default_cfg)
            self._register_alias("default", default_name)

        self._discover_env_sites(base)
        self._load_alias_json()

        if not self._sites:
            raise ToolError(
                "No Weka sites configured. Set WEKA_HOST/WEKA_PASSWORD for a "
                "single cluster, or WEKA_<SITE>_URL/WEKA_<SITE>_ADMIN_PASSWORD "
                "for multi-site discovery."
            )

        if has_base:
            self._active_key = self.resolve(default_name)
        else:
            self._active_key = next(iter(self._sites))

    def _discover_env_sites(self, base: Settings) -> None:
        """Auto-discover sites from WEKA_<SITE>_URL environment variables.

        Handles env aliases: ADMIN/USERNAME for user, ADMIN_PASSWORD/PASSWORD
        for password. Strips trailing /ui from URLs. Skips sites without passwords.
        """
        base_password = (
            base.weka_password.get_secret_value() if base.weka_password is not None else None
        )

        for env_key, env_val in os.environ.items():
            m = re.fullmatch(r"WEKA_([A-Z0-9_]+)_URL", env_key)
            if not m:
                continue
            site_name = m.group(1)
            site_k = _site_key(site_name)
            if site_k in self._sites:
                continue

            username = (
                os.environ.get(f"WEKA_{site_name}_ADMIN")
                or os.environ.get(f"WEKA_{site_name}_USERNAME")
                or base.weka_username
            )
            password = (
                os.environ.get(f"WEKA_{site_name}_ADMIN_PASSWORD")
                or os.environ.get(f"WEKA_{site_name}_PASSWORD")
                or base_password
            )
            if password is None:
                logger.warning(
                    "Skipping site %s: no password found (set WEKA_%s_ADMIN_PASSWORD)",
                    site_k,
                    site_name,
                )
                continue
            org = os.environ.get(f"WEKA_{site_name}_ORG") or base.weka_org
            verify = _env_bool(f"WEKA_{site_name}_VERIFY_SSL", base.verify_ssl)
            timeout = float(
                os.environ.get(f"WEKA_{site_name}_TIMEOUT_SECONDS", str(base.timeout_seconds))
            )
            api_path = os.environ.get(f"WEKA_{site_name}_API_BASE_PATH", base.api_base_path)

            url = env_val.rstrip("/")
            if url.endswith("/ui"):
                url = url[: -len("/ui")]

            cfg = WekaSiteConfig(
                site=site_k,
                weka_host=url,
                username=username,
                password=password,
                org=org,
                verify_ssl=verify,
                timeout_seconds=timeout,
                api_base_path=api_path,
            )
            self.register_site(cfg)

    def resolve(self, site: str | None = None) -> str:
        """Resolve a site key/alias to the canonical site key.

        If site is None/empty, returns the active site key.
        Raises ToolError on unknown sites (wraps base KeyError).
        """
        if site is None or not site.strip():
            if self._active_key:
                return self._active_key
            raise ToolError("No active Weka site configured")

        try:
            return super().resolve(site)
        except KeyError as e:
            raise ToolError(str(e)) from None

    def set_active(self, site: str) -> WekaSiteConfig:
        """Explicitly set the active site. Returns the site config."""
        key = self.resolve(site)
        self._active_key = key
        return self._sites[key]

    def get_client(self, site: str | None = None) -> WekaRestClient:
        """Get the WekaRestClient for a site, creating lazily on first use."""
        key = self.resolve(site)
        if key not in self._clients:
            cfg = self._sites[key]
            self._clients[key] = WekaRestClient(
                host=cfg.weka_host,
                username=cfg.username,
                password=cfg.password,
                org=cfg.org,
                api_base_path=cfg.api_base_path,
                verify_ssl=cfg.verify_ssl,
                timeout_seconds=cfg.timeout_seconds,
            )
        return self._clients[key]

    def get_config(self, site: str | None = None) -> WekaSiteConfig:
        """Get the WekaSiteConfig for a site without changing active site."""
        key = self.resolve(site)
        return self._sites[key]

    def list_sites(self) -> list[dict[str, Any]]:
        """Return site info suitable for weka_list_sites output."""
        return [
            {
                "site": key,
                "weka_host": cfg.weka_host,
                "username": cfg.username,
                "org": cfg.org,
                "verify_ssl": cfg.verify_ssl,
                "timeout_seconds": cfg.timeout_seconds,
                "active": key == self._active_key,
            }
            for key, cfg in sorted(self._sites.items())
        ]

    def close_all(self) -> None:
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass


# Backward-compatible alias
SiteManager = WekaSiteManager


def _site_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}
