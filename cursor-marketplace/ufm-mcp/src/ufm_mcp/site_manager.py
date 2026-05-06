"""Multi-site UFM client management.

Replaces the previous module-level globals with a proper class that
resolves sites without implicitly mutating global state.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError

from ufm_mcp.config import Settings
from ufm_mcp.ufm_client import UfmRestClient

logger = logging.getLogger(__name__)


@dataclass
class SiteConfig:
    """Per-site configuration and path overrides."""

    site: str
    ufm_url: str
    ufm_token: str | None
    verify_ssl: bool
    timeout_seconds: float
    ufm_api_base_path: str
    ufm_resources_base_path: str
    ufm_logs_base_path: str
    ufm_web_base_path: str
    ufm_backup_base_path: str
    ufm_jobs_base_path: str

    def __post_init__(self) -> None:
        for field in (
            "ufm_api_base_path",
            "ufm_resources_base_path",
            "ufm_logs_base_path",
            "ufm_web_base_path",
            "ufm_backup_base_path",
            "ufm_jobs_base_path",
        ):
            setattr(self, field, getattr(self, field).rstrip("/"))


class SiteManager:
    """Manages multi-site UFM connections.

    Sites are discovered from:
    1. The base Settings (becomes the "default" site)
    2. Environment variables like UFM_<SITE>_URL / UFM_<SITE>_TOKEN
    3. Optional alias mapping via UFM_SITE_ALIASES_JSON
    """

    def __init__(self) -> None:
        self._sites: dict[str, SiteConfig] = {}
        self._clients: dict[str, UfmRestClient] = {}
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
        token = base.ufm_token.get_secret_value() if base.ufm_token else None
        default_name = _site_key(os.environ.get("UFM_DEFAULT_SITE", "default"))

        default_cfg = SiteConfig(
            site=default_name,
            ufm_url=str(base.ufm_url),
            ufm_token=token,
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

        self._discover_env_sites(base)
        self._load_alias_json()

        self._active_key = self.resolve(default_name)

    def _discover_env_sites(self, base: Settings) -> None:
        """Auto-discover sites from UFM_<SITE>_URL environment variables."""
        for env_key, env_val in os.environ.items():
            m = re.fullmatch(r"UFM_([A-Z0-9_]+)_URL", env_key)
            if not m:
                continue
            site_name = m.group(1)
            site_k = _site_key(site_name)
            if site_k in self._sites:
                continue

            site_token = os.environ.get(f"UFM_{site_name}_TOKEN") or os.environ.get(
                f"UFM_{site_name}_ACCESS_TOKEN"
            )
            verify = _env_bool(f"UFM_{site_name}_VERIFY_SSL", base.verify_ssl)
            timeout = float(
                os.environ.get(f"UFM_{site_name}_TIMEOUT_SECONDS", str(base.timeout_seconds))
            )

            cfg = SiteConfig(
                site=site_k,
                ufm_url=env_val,
                ufm_token=site_token,
                verify_ssl=verify,
                timeout_seconds=timeout,
                ufm_api_base_path=os.environ.get(
                    f"UFM_{site_name}_API_BASE_PATH", base.ufm_api_base_path
                ),
                ufm_resources_base_path=os.environ.get(
                    f"UFM_{site_name}_RESOURCES_BASE_PATH", base.ufm_resources_base_path
                ),
                ufm_logs_base_path=os.environ.get(
                    f"UFM_{site_name}_LOGS_BASE_PATH", base.ufm_logs_base_path
                ),
                ufm_web_base_path=os.environ.get(
                    f"UFM_{site_name}_WEB_BASE_PATH", base.ufm_web_base_path
                ),
                ufm_backup_base_path=os.environ.get(
                    f"UFM_{site_name}_BACKUP_BASE_PATH", base.ufm_backup_base_path
                ),
                ufm_jobs_base_path=os.environ.get(
                    f"UFM_{site_name}_JOBS_BASE_PATH", base.ufm_jobs_base_path
                ),
            )
            self._register_site(cfg)

    def _load_alias_json(self) -> None:
        raw = os.environ.get("UFM_SITE_ALIASES_JSON", "").strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for alias, target in parsed.items():
                    if isinstance(alias, str) and isinstance(target, str):
                        self._register_alias(alias, target)
        except Exception:
            logger.warning("Ignoring invalid UFM_SITE_ALIASES_JSON")

    def _register_site(self, cfg: SiteConfig) -> None:
        self._sites[cfg.site] = cfg
        self._clients[cfg.site] = UfmRestClient(
            base_url=cfg.ufm_url,
            token=cfg.ufm_token,
            verify_ssl=cfg.verify_ssl,
            timeout_seconds=cfg.timeout_seconds,
        )
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
            raise ToolError("No active UFM site configured")

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

    def get_client(self, site: str | None = None) -> UfmRestClient:
        """Get the UfmRestClient for a site without changing active site."""
        key = self.resolve(site)
        return self._clients[key]

    def get_config(self, site: str | None = None) -> SiteConfig:
        """Get the SiteConfig for a site without changing active site."""
        key = self.resolve(site)
        return self._sites[key]

    def get_effective_summary(self) -> dict[str, Any]:
        """Return a summary suitable for ufm_get_config output."""
        cfg = self.get_config()
        return {
            "active_site": self._active_key,
            "sites": sorted(self._sites.keys()),
            "site_aliases": dict(sorted(self._aliases.items())),
            "ufm_url": cfg.ufm_url,
            "ufm_token": "***REDACTED***" if cfg.ufm_token else None,
            "verify_ssl": cfg.verify_ssl,
            "timeout_seconds": cfg.timeout_seconds,
            "ufm_api_base_path": cfg.ufm_api_base_path,
            "ufm_resources_base_path": cfg.ufm_resources_base_path,
            "ufm_logs_base_path": cfg.ufm_logs_base_path,
            "ufm_web_base_path": cfg.ufm_web_base_path,
            "ufm_backup_base_path": cfg.ufm_backup_base_path,
            "ufm_jobs_base_path": cfg.ufm_jobs_base_path,
        }

    def list_sites(self) -> list[dict[str, Any]]:
        """Return site info suitable for ufm_list_sites output."""
        return [
            {
                "site": key,
                "ufm_url": cfg.ufm_url,
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
