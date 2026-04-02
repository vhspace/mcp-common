"""Generic multi-site configuration manager.

Extracted from the ufm-mcp SiteManager pattern. Provides a reusable base for
any MCP server that connects to multiple instances of the same service,
discovered from environment variables with a common prefix.

Environment variable conventions (where ``PREFIX`` is the ``env_prefix``):

    {PREFIX}_{SITE}_URL          – required, triggers site auto-discovery
    {PREFIX}_{SITE}_{FIELD}      – any other field on the SiteConfig subclass
    {PREFIX}_SITE_ALIASES_JSON   – ``{"alias": "canonical_site"}`` mapping
    {PREFIX}_DEFAULT_SITE        – canonical name of the default site
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Generic, TypeVar, get_type_hints

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="SiteConfig")


class SiteConfig(BaseModel):
    """Base per-site configuration.

    Subclass this and add fields specific to your service::

        class WekaSiteConfig(SiteConfig):
            url: str
            username: str
            password: str
            org: str | None = None
    """

    site: str


class SiteManager(Generic[T]):
    """Multi-site configuration manager.

    Discovers sites from environment variables using a configurable prefix.
    Each site is represented by a ``SiteConfig`` subclass instance.

    Usage::

        class WekaSiteConfig(SiteConfig):
            url: str
            username: str
            password: str
            org: str | None = None

        class WekaSiteManager(SiteManager[WekaSiteConfig]):
            env_prefix = "WEKA"

        mgr = WekaSiteManager(WekaSiteConfig)
        mgr.discover()
        cfg = mgr.get_site("prod")
    """

    env_prefix: str = "MCP"

    def __init__(self, config_cls: type[T]) -> None:
        self._config_cls = config_cls
        self._sites: dict[str, T] = {}
        self._aliases: dict[str, str] = {}
        self._default_site: str | None = None

    @property
    def default_site(self) -> str | None:
        return self._default_site

    def discover(self, *, defaults: dict[str, Any] | None = None) -> None:
        """Auto-discover sites from environment variables.

        Scans for ``{PREFIX}_{SITE}_URL`` to identify sites, then collects
        all ``{PREFIX}_{SITE}_{FIELD}`` vars matching fields on the config class.

        Parameters
        ----------
        defaults
            Optional dict of default field values used when an env var
            is not set for that field.
        """
        defaults = defaults or {}
        prefix = self.env_prefix.upper()
        url_pattern = re.compile(rf"^{re.escape(prefix)}_([A-Z0-9][A-Z0-9_]*)_URL$")

        config_fields = self._get_config_fields()

        for env_key, env_val in sorted(os.environ.items()):
            m = url_pattern.match(env_key)
            if not m:
                continue

            raw_site_name = m.group(1)
            site_key = _normalize_key(raw_site_name)
            if site_key in self._sites:
                continue

            field_values: dict[str, Any] = {"site": site_key}
            for field_name in config_fields:
                env_name = f"{prefix}_{raw_site_name}_{field_name.upper()}"
                val = os.environ.get(env_name)
                if val is not None:
                    field_values[field_name] = val
                elif field_name in defaults:
                    field_values[field_name] = defaults[field_name]

            if "url" not in field_values:
                field_values["url"] = env_val

            try:
                cfg = self._config_cls(**field_values)
            except Exception:
                logger.warning("Skipping site %r: invalid configuration", site_key)
                continue

            self._register_site(cfg)

        self._load_alias_json()
        self._load_default_site()

    def register_site(self, config: T) -> None:
        """Manually register a site configuration."""
        self._register_site(config)

    def _register_site(self, config: T) -> None:
        key = _normalize_key(config.site)
        config.site = key
        self._sites[key] = config
        for alias in _auto_aliases(key):
            self._register_alias(alias, key)

    def _register_alias(self, alias: str, target: str) -> None:
        a = _normalize_key(alias)
        t = _normalize_key(target)
        if not a or not t:
            return
        existing = self._aliases.get(a)
        if existing and existing != t:
            logger.debug("Alias %r already points to %r; ignoring mapping to %r", a, existing, t)
            return
        self._aliases[a] = t

    def _load_alias_json(self) -> None:
        raw = os.environ.get(f"{self.env_prefix.upper()}_SITE_ALIASES_JSON", "").strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for alias, target in parsed.items():
                    if isinstance(alias, str) and isinstance(target, str):
                        self._register_alias(alias, target)
        except Exception:
            logger.warning("Ignoring invalid %s_SITE_ALIASES_JSON", self.env_prefix.upper())

    def _load_default_site(self) -> None:
        env_default = os.environ.get(f"{self.env_prefix.upper()}_DEFAULT_SITE", "").strip()
        if env_default:
            self._default_site = _normalize_key(env_default)
        elif self._sites:
            self._default_site = next(iter(self._sites))

    def _get_config_fields(self) -> set[str]:
        """Return field names from the config class, excluding 'site'."""
        try:
            hints = get_type_hints(self._config_cls)
        except Exception:
            hints = {}
        fields = set(hints.keys()) | set(self._config_cls.model_fields.keys())
        fields.discard("site")
        return fields

    def get_site(self, name: str | None = None) -> T:
        """Resolve a site name (or alias) and return its config.

        If *name* is ``None`` or empty, returns the default site.

        Raises
        ------
        KeyError
            If the site is not found.
        """
        key = self._resolve(name)
        return self._sites[key]

    def list_sites(self) -> dict[str, T]:
        """Return all registered sites as ``{site_key: config}``."""
        return self.sites

    def resolve(self, name: str | None = None) -> str:
        """Resolve a site name/alias to the canonical key.

        Raises ``KeyError`` on unknown sites.
        """
        return self._resolve(name)

    def _resolve(self, name: str | None) -> str:
        if name is None or not name.strip():
            if self._default_site and self._default_site in self._sites:
                return self._default_site
            alias_target = self._aliases.get("default")
            if alias_target and alias_target in self._sites:
                return alias_target
            if self._sites:
                return next(iter(self._sites))
            raise KeyError("No sites configured")

        key = _normalize_key(name)
        if key in self._sites:
            return key
        mapped = self._aliases.get(key)
        if mapped and mapped in self._sites:
            return mapped
        known = sorted(self._sites.keys())
        raise KeyError(f"Unknown site {name!r}. Known sites: {known}")

    @property
    def sites(self) -> dict[str, T]:
        return dict(self._sites)

    @property
    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)


def _normalize_key(raw: str) -> str:
    """Normalize a site key to lowercase alphanumeric + underscores."""
    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")


def _auto_aliases(site: str) -> set[str]:
    """Generate automatic short aliases for a site key."""
    s = _normalize_key(site)
    out = {s}
    parts = [p for p in s.split("_") if p]
    if parts:
        out.add(parts[-1])
    if len(parts) >= 2:
        out.add("_".join(parts[-2:]))
    return {x for x in out if x}
