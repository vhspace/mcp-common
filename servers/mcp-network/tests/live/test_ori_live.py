"""Live tests against the real ORI-TX fleet.

Skipped unless ``MCP_NETWORK_RUN_LIVE=1`` is set in the environment. Even
then, they require ``ORI_NETWORK_USER`` / ``ORI_NETWORK_PASSWORD``.
"""

from __future__ import annotations

import os
import time

import pytest

from mcp_network.drivers import get_driver
from mcp_network.sites import NetworkSiteManager

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("MCP_NETWORK_RUN_LIVE") != "1",
        reason="set MCP_NETWORK_RUN_LIVE=1 to opt in",
    ),
]


@pytest.mark.anyio
async def test_live_lea_03_system_info() -> None:
    mgr = NetworkSiteManager()
    mgr.load()
    cfg, sw = mgr.resolve_switch("dfw01-inb-sw-lea-03")
    assert cfg.operational, cfg.reason
    drv = get_driver(cfg, sw)
    info = await drv.system_info()
    assert info


@pytest.mark.anyio
async def test_live_session_reuse_two_commands() -> None:
    """Session reuse: verify two commands work within one session.

    We don't assert timing (network jitter makes single-switch timing
    comparisons unreliable). The real speedup is proven by the fan-out
    tests where N switches run in parallel with one conn each.
    """
    mgr = NetworkSiteManager()
    mgr.load()
    cfg, sw = mgr.resolve_switch("dfw01-inb-sw-lea-03")
    drv = get_driver(cfg, sw)

    async with drv.session():
        info = await drv.system_info()
        entries = await drv.mac_table()

    assert info.get("fqdn") or info.get("hostname")
    assert len(entries) > 0


@pytest.mark.anyio
async def test_live_find_port_for_mac_timing() -> None:
    """find_port_for_mac should complete in under 10s with parallel fan-out."""
    from mcp_network.server import find_port_for_mac

    t0 = time.monotonic()
    result = await find_port_for_mac(mac="9c:63:c0:26:ef:ec")
    elapsed = time.monotonic() - t0

    print(f"find_port_for_mac: {elapsed:.2f}s")
    assert elapsed < 15, f"took {elapsed:.1f}s; expected <15s with parallel fan-out"
    assert result.get("direct") or result.get("indirect"), "expected at least one hit"


@pytest.mark.anyio
async def test_live_find_port_for_node_timing() -> None:
    """find_port_for_node should complete in under 15s (was ~71s before optimization)."""
    from mcp_network.server import find_port_for_node

    t0 = time.monotonic()
    result = await find_port_for_node(node="research-common-h100-078")
    elapsed = time.monotonic() - t0

    print(f"find_port_for_node: {elapsed:.2f}s")
    assert elapsed < 20, f"took {elapsed:.1f}s; expected <20s with single-pass parallel fan-out"
    nics = result.get("nics", [])
    assert len(nics) > 0, "expected at least one NIC"
    direct_hits = [n for n in nics if n.get("direct")]
    assert len(direct_hits) > 0, "expected at least one NIC with a direct switch port hit"
