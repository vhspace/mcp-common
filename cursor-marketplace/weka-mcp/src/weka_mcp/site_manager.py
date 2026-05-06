"""Multi-site Weka client management.

Discovers and manages connections to multiple Weka clusters from a single
MCP server instance. Follows the same pattern as ufm-mcp's site_manager.

Sites are discovered from:
1. The base Settings (becomes the "default" site)
2. Environment variables like WEKA_<SITE>_URL / WEKA_<SITE>_ADMIN
3. Optional alias mapping via WEKA_SITE_ALIASES_JSON
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError

from weka_mcp.config import Settings
from weka_mcp.weka_client import WekaRestClient

logger = logging.getLogger(__name__)


@dataclass
class SiteConfig:
    """Per-site connection configuration."""

    site: str
    weka_host: str
    username: str
    password: str
    org: str | None
    verify_ssl: bool
    timeout_seconds: float
    api_base_path: str


class SiteManager:
    """Manages multi-site Weka connections.

    Sites are discovered from:
    1. The base Settings (becomes the "default" site)
    2. Environment variables like WEKA_<SITE>_URL
    3. Optional alias mapping via WEKA_SITE_ALIASES_JSON
    """

    def __init__(self) -> None:
        self._sites: dict[str, SiteConfig] = {}
        self._clients: dict[str, WekaRestClient] = {}
        self._aliases: dict[str, str] = {}
        self._active_key: str | None = None

    @property
    def active_key(self) -> str | None:
        return self._active_key

    @property
    def sites(self) -> dict[str, SiteConfig]:
        return dict(self._sites)

    @property
    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def configure(self, base: Settings) -> None:
        """Initialize sites from base settings + environment."""
        default_name = _site_key(os.environ.get("WEKA_DEFAULT_SITE", "default"))

        default_cfg = SiteConfig(
            site=default_name,
            weka_host=str(base.weka_host),
            username=base.weka_username,
            password=base.weka_password.get_secret_value(),
            org=base.weka_org,
            verify_ssl=base.verify_ssl,
            timeout_seconds=base.timeout_seconds,
            api_base_path=base.api_base_path,
        )
        self._register_site(default_cfg)
        self._register_alias("default", default_name)

        self._discover_env_sites(base)
        self._load_alias_json()

        self._active_key = self.resolve(default_name)

    def _discover_env_sites(self, base: Settings) -> None:
        """Auto-discover sites from WEKA_<SITE>_URL environment variables."""
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
                or base.weka_password.get_secret_value()
            )
            org = os.environ.get(f"WEKA_{site_name}_ORG") or base.weka_org
            verify = _env_bool(f"WEKA_{site_name}_VERIFY_SSL", base.verify_ssl)
            timeout = float(
                os.environ.get(f"WEKA_{site_name}_TIMEOUT_SECONDS", str(base.timeout_seconds))
            )
            api_path = os.environ.get(f"WEKA_{site_name}_API_BASE_PATH", base.api_base_path)

            url = env_val.rstrip("/")
            if url.endswith("/ui"):
                url = url[: -len("/ui")]

            cfg = SiteConfig(
                site=site_k,
                weka_host=url,
                username=username,
                password=password,
                org=org,
                verify_ssl=verify,
                timeout_seconds=timeout,
                api_base_path=api_path,
            )
            self._register_site(cfg)

    def _load_alias_json(self) -> None:
        raw = os.environ.get("WEKA_SITE_ALIASES_JSON", "").strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for alias, target in parsed.items():
                    if isinstance(alias, str) and isinstance(target, str):
                        self._register_alias(alias, target)
        except Exception:
            logger.warning("Ignoring invalid WEKA_SITE_ALIASES_JSON")

    def _register_site(self, cfg: SiteConfig) -> None:
        self._sites[cfg.site] = cfg
        for alias in _aliases_for_site(cfg.site):
            self._register_alias(alias, cfg.site)

    def _register_alias(self, alias: str, target: str) -> None:
        a = _site_key(alias)
        t = _site_key(target)
        if a and t:
            self._aliases[a] = t

    def resolve(self, site: str | None) -> str:
        """Resolve a site key/alias to the canonical site key.

        If site is None/empty, returns the active site key.
        Does NOT change the active site as a side-effect.
        """
        if site is None or not site.strip():
            if self._active_key:
                return self._active_key
            raise ToolError("No active Weka site configured")

        key = _site_key(site)
        if key in self._sites:
            return key
        mapped = self._aliases.get(key)
        if mapped and mapped in self._sites:
            return mapped

        known = sorted(self._sites.keys())
        raise ToolError(f"Unknown site {site!r}. Known sites: {known}")

    def set_active(self, site: str) -> SiteConfig:
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

    def get_config(self, site: str | None = None) -> SiteConfig:
        """Get the SiteConfig for a site without changing active site."""
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


def _site_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")


def _aliases_for_site(site: str) -> set[str]:
    s = _site_key(site)
    out = {s}
    parts = [p for p in s.split("_") if p]
    if parts:
        out.add(parts[-1])
    if len(parts) >= 2:
        out.add("_".join(parts[-2:]))
    return {x for x in out if x}


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}
