"""Switch driver factory.

Drivers are selected via ``NetworkSiteConfig.driver`` (itself driven by the
site inventory's ``driver`` field). Only ``cumulus`` is implemented today;
this module is the seam for future Arista/Junos/SONiC backends.
"""

from __future__ import annotations

from mcp_network.drivers.base import (
    ConnectionInfo,
    NetworkDriverError,
    SwitchDriver,
)
from mcp_network.drivers.cumulus import CumulusDriver
from mcp_network.inventory import SwitchEntry
from mcp_network.sites import NetworkSiteConfig

__all__ = [
    "ConnectionInfo",
    "CumulusDriver",
    "NetworkDriverError",
    "SwitchDriver",
    "get_driver",
]


def get_driver(site_cfg: NetworkSiteConfig, switch: SwitchEntry) -> SwitchDriver:
    """Build the driver for one switch of a site.

    Raises ``NetworkDriverError`` if the site is not operational (missing
    creds) or the driver is unsupported.
    """
    if not site_cfg.operational:
        raise NetworkDriverError(
            f"site {site_cfg.site!r} not operational: {site_cfg.reason}",
            host=switch.connect_host,
            hint="populate the env vars named in the site's credentials_env",
        )
    assert site_cfg.user is not None  # operational implies both set
    assert site_cfg.password is not None

    jump_host = None
    jump_port = 22
    jump_user: str | None = None
    jump_password = None
    if site_cfg.inventory.jump_host is not None:
        jump_host = site_cfg.inventory.jump_host.host
        jump_port = site_cfg.inventory.jump_host.port
        jump_user = site_cfg.jump_user or site_cfg.user
        jump_password = site_cfg.jump_password or site_cfg.password

    conn = ConnectionInfo(
        host=switch.connect_host,
        user=site_cfg.user,
        password=site_cfg.password,
        port=site_cfg.inventory.ssh_port,
        jump_host=jump_host,
        jump_port=jump_port,
        jump_user=jump_user,
        jump_password=jump_password,
    )

    driver = site_cfg.driver
    if driver == "cumulus":
        return CumulusDriver(conn)
    raise NetworkDriverError(
        f"unsupported driver {driver!r}",
        host=switch.connect_host,
        hint="only 'cumulus' is implemented in v1",
    )
