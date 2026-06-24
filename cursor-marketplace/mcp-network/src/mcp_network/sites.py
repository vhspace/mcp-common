"""Per-site configuration and lookup.

Wraps each ``SiteInventory`` in a ``NetworkSiteConfig`` (inventory + resolved
credentials + operational flag), and provides a ``NetworkSiteManager`` on top
of ``mcp_common.SiteManager`` that sources its sites from
``InventoryLoader`` rather than env-var discovery.

Why not ``SiteManager.discover()``? That helper keys discovery on
``{PREFIX}_{SITE}_URL`` env vars, which we don't have — our fleet is defined
in JSON, not env.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from mcp_common import SiteConfig, SiteManager
from pydantic import ConfigDict, Field, SecretStr

from mcp_network.inventory import (
    InventoryLoader,
    SiteInventory,
    SwitchEntry,
)

logger = logging.getLogger(__name__)


class NetworkSiteConfig(SiteConfig):
    """Runtime config for one site: the parsed inventory plus resolved creds."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inventory: SiteInventory
    user: str | None = None
    password: SecretStr | None = None
    jump_user: str | None = None
    jump_password: SecretStr | None = None
    operational: bool = True
    reason: str | None = Field(
        default=None,
        description="If not operational, why (e.g. 'missing env var FOO_PASSWORD').",
    )

    @property
    def driver(self) -> Literal["cumulus"]:
        return self.inventory.driver

    @property
    def switches(self) -> list[SwitchEntry]:
        return self.inventory.switches

    def find_switch(self, name_or_ip: str) -> SwitchEntry | None:
        return self.inventory.find_switch(name_or_ip)


def _build_site_config(inv: SiteInventory) -> NetworkSiteConfig:
    """Resolve credentials from env and return a ``NetworkSiteConfig``."""
    user, password = inv.resolve_credentials()
    jump_user, jump_pw = inv.resolve_jump_credentials()

    reason: str | None = None
    operational = True
    if user is None:
        operational = False
        reason = f"env var {inv.credentials_env.user} is not set"
    elif password is None:
        operational = False
        reason = f"env var {inv.credentials_env.password} is not set"

    return NetworkSiteConfig(
        site=inv.site,
        inventory=inv,
        user=user,
        password=password,
        jump_user=jump_user,
        jump_password=jump_pw,
        operational=operational,
        reason=reason,
    )


class NetworkSiteManager(SiteManager[NetworkSiteConfig]):
    """Multi-site manager for mcp-network.

    Sites come from inventory JSON files, not env var discovery. Aliases and
    default-site selection still follow the ``mcp_common.SiteManager``
    conventions (``NETWORK_DEFAULT_SITE``, ``NETWORK_SITE_ALIASES_JSON``).
    """

    env_prefix = "NETWORK"

    def __init__(self) -> None:
        super().__init__(NetworkSiteConfig)

    def load(self, inventory_dir: Path | None = None) -> None:
        """Load inventory files and register each site.

        Resolves the default site with this precedence:
        1. ``NETWORK_DEFAULT_SITE`` env var (if it names a registered site)
        2. Any site whose inventory sets ``default: true``
        3. First site in sort order (inherited from SiteManager)
        """
        loader = InventoryLoader(inventory_dir)
        inventories = loader.load_dir()
        json_default: str | None = None

        for inv in inventories:
            cfg = _build_site_config(inv)
            self.register_site(cfg)
            for alias in inv.aliases:
                self._register_alias(alias, cfg.site)
            if inv.default and json_default is None:
                json_default = cfg.site
            if not cfg.operational:
                logger.warning("Site %r registered but not operational: %s", cfg.site, cfg.reason)

        self._load_alias_json()
        env_default = os.environ.get(f"{self.env_prefix.upper()}_DEFAULT_SITE", "").strip()
        if env_default:
            self._default_site = _normalize(env_default)
        elif json_default:
            self._default_site = _normalize(json_default)
        elif self._sites:
            self._default_site = next(iter(self._sites))

    def resolve_switch(
        self, name_or_ip: str, site: str | None = None
    ) -> tuple[NetworkSiteConfig, SwitchEntry]:
        """Resolve ``name_or_ip`` to ``(site_cfg, switch_entry)``.

        If ``site`` is provided, look only there; otherwise search every
        registered site. Raises ``KeyError`` if no match.
        """
        if site is not None:
            cfg = self.get_site(site)
            sw = cfg.find_switch(name_or_ip)
            if sw is None:
                raise KeyError(f"Switch {name_or_ip!r} not found in site {cfg.site!r}")
            return cfg, sw

        matches: list[tuple[NetworkSiteConfig, SwitchEntry]] = []
        for cfg in self._sites.values():
            sw = cfg.find_switch(name_or_ip)
            if sw is not None:
                matches.append((cfg, sw))
        if not matches:
            raise KeyError(f"Switch {name_or_ip!r} not found in any registered site")
        if len(matches) > 1:
            sites = [cfg.site for cfg, _ in matches]
            raise KeyError(
                f"Switch {name_or_ip!r} is ambiguous across sites {sites}; "
                "pass site= to disambiguate"
            )
        return matches[0]


def _normalize(raw: str) -> str:
    """Mirror SiteManager._normalize_key for default-site lookup."""
    import re

    return re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
