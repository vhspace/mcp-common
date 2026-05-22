"""Multi-instance MAAS site manager using mcp_common.SiteManager.

Discovers MAAS instances from three sources (in precedence order):
1. Prefixed env vars: MAAS_{SITE}_URL + MAAS_{SITE}_API_KEY (or MAAS_{SITE}_API)
2. JSON config: MAAS_INSTANCES env var
3. Single-instance: MAAS_URL + MAAS_API_KEY (registered as "default")

Per-site DB URLs (MAAS_{SITE}_DB_URL) are tracked separately.
"""

from __future__ import annotations

import json
import logging
import os
import re

from mcp_common import SiteConfig, SiteManager

logger = logging.getLogger(__name__)

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_API_KEY_TEMPLATES = ("MAAS_{}_API_KEY", "MAAS_{}_API")


def _ensure_scheme(url: str) -> str:
    """Prepend http:// if the URL has no scheme."""
    url = url.strip()
    if not _SCHEME_RE.match(url):
        url = f"http://{url}"
    return url


class MaasSiteConfig(SiteConfig):
    """Per-MAAS-instance connection details."""

    url: str
    api_key: str


class MaasSiteManager(SiteManager[MaasSiteConfig]):
    """Multi-instance MAAS manager.

    Discovers instances from three sources (in precedence order):
    1. Prefixed env vars: MAAS_{SITE}_URL + MAAS_{SITE}_API_KEY
    2. JSON config: MAAS_INSTANCES env var
    3. Single-instance: MAAS_URL + MAAS_API_KEY (registered as "default")

    After merging, MAAS_DEFAULT_SITE aliases the named site to "default",
    and MAAS_SITE_ALIASES_JSON entries are registered.
    """

    env_prefix = "MAAS"

    def __init__(self) -> None:
        super().__init__(MaasSiteConfig)
        self._db_urls: dict[str, str] = {}

    @property
    def db_urls(self) -> dict[str, str]:
        """Per-site DB URLs (MAAS_{SITE}_DB_URL)."""
        return dict(self._db_urls)

    def configure(self) -> None:
        """Load all instance sources and merge.

        Call this instead of ``discover()`` to get the full MAAS-specific
        behavior including JSON instances and single-instance fallback.
        """
        self._discover_prefixed_instances()
        self._load_json_instances()
        self._load_single_instance()
        self._load_alias_json()
        self._load_default_site()
        self._promote_default_site()
        self._discover_db_urls()

    def _discover_prefixed_instances(self) -> None:
        """Scan env for MAAS_{SITE}_URL + MAAS_{SITE}_API[_KEY] pairs."""
        url_pattern = re.compile(r"^MAAS_([A-Z0-9][A-Z0-9_]*)_URL$", re.IGNORECASE)

        for key in sorted(os.environ):
            m = url_pattern.match(key)
            if not m:
                continue

            raw_site = m.group(1)
            # Skip the single-instance case (no site prefix)
            if raw_site.upper() in ("DB", "DEFAULT", "SITE", "MCP", "HTTP"):
                continue

            url = os.environ.get(key, "").strip()
            if not url:
                continue

            api_key = self._find_api_key(raw_site)
            if not api_key:
                logger.debug("Skipping MAAS_%s: no API key found", raw_site)
                continue

            site_key = raw_site.lower()
            try:
                cfg = MaasSiteConfig(
                    site=site_key,
                    url=_ensure_scheme(url),
                    api_key=api_key,
                )
                self.register_site(cfg)
            except Exception:
                logger.warning("Skipping MAAS site %r: invalid configuration", site_key)

    def _find_api_key(self, raw_site: str) -> str | None:
        """Find API key for a site, checking both _API_KEY and _API suffixes."""
        for template in _API_KEY_TEMPLATES:
            candidate = template.format(raw_site)
            for env_key in (candidate, candidate.upper()):
                val = os.environ.get(env_key)
                if val:
                    return val
        return None

    def _load_json_instances(self) -> None:
        """Parse MAAS_INSTANCES JSON env var and register each entry."""
        raw = os.environ.get("MAAS_INSTANCES", "").strip()
        if not raw:
            return

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Ignoring invalid MAAS_INSTANCES JSON: %s", e)
            return

        if not isinstance(parsed, dict):
            logger.warning("MAAS_INSTANCES must be a JSON object")
            return

        for name, config in parsed.items():
            if not isinstance(config, dict):
                continue
            url = config.get("url", "")
            api_key = config.get("api_key", "")
            if not url or not api_key:
                logger.warning("MAAS_INSTANCES[%r]: missing url or api_key", name)
                continue

            site_key = name.lower()
            try:
                cfg = MaasSiteConfig(
                    site=site_key,
                    url=_ensure_scheme(url),
                    api_key=api_key,
                )
                self.register_site(cfg)
            except Exception:
                logger.warning("Skipping MAAS_INSTANCES[%r]: invalid config", name)

    def _load_single_instance(self) -> None:
        """Register MAAS_URL + MAAS_API_KEY as the "default" site."""
        url = os.environ.get("MAAS_URL") or os.environ.get("MAAS_HOST")
        api_key = os.environ.get("MAAS_API_KEY")
        if not url or not api_key:
            return

        try:
            cfg = MaasSiteConfig(
                site="default",
                url=_ensure_scheme(url.strip()),
                api_key=api_key.strip(),
            )
            self.register_site(cfg)
        except Exception:
            logger.warning("Skipping single-instance MAAS_URL: invalid config")

    def _promote_default_site(self) -> None:
        """If MAAS_DEFAULT_SITE is set, register "default" as an alias."""
        if self._default_site and self._default_site != "default":
            if self._default_site in self._sites and "default" not in self._sites:
                self._register_alias("default", self._default_site)

    def _discover_db_urls(self) -> None:
        """Collect per-site MAAS_{SITE}_DB_URL env vars."""
        default_db = os.environ.get("MAAS_DB_URL") or os.environ.get("MAAS_DATABASE_URL")
        if default_db:
            self._db_urls["default"] = default_db

        for site_key in self._sites:
            if site_key == "default":
                continue
            env_key = f"MAAS_{site_key.upper()}_DB_URL"
            val = os.environ.get(env_key)
            if val:
                self._db_urls[site_key] = val

        if self._default_site and self._default_site in self._db_urls and "default" not in self._db_urls:
            self._db_urls["default"] = self._db_urls[self._default_site]

    def get_instances_dict(self) -> dict[str, dict[str, str]]:
        """Return a dict compatible with the old _discover_prefixed_instances() output.

        Returns {site_name: {"url": ..., "api_key": ...}} for all registered sites.
        """
        return {
            name: {"url": cfg.url, "api_key": cfg.api_key}
            for name, cfg in self._sites.items()
        }

    def has_sites(self) -> bool:
        """Return True if at least one site is configured."""
        return bool(self._sites)
