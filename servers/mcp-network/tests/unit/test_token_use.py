"""Tests for the token-use projections, limits, and CLI formatters.

These cover the work from the mcp-network token-use review:

* the ``brief=True`` projections (``get_port_status`` all-ports,
  ``get_lldp_neighbors``, ``get_bgp_neighbors``) mirroring ``get_system_info``;
* the ``get_mac_table`` / ``get_wjh`` ``limit`` + ``count_only`` semantics and
  MAC-filter normalization;
* the pure projection helpers; and
* the human-output ``formatters`` ``network-cli`` uses (formatters are bypassed
  in ``--json`` / non-TTY mode, so they are exercised directly here).

The tools are called directly (``@dual_mode_tool`` returns the original async
function) against the bundled ORI-TX inventory loaded at import time, with the
switch driver mocked to avoid live SSH.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from mcp_network import server

# A leaf switch in the bundled ORI-TX inventory. ``swp29`` is a configured leaf
# uplink there, so the port classification is deterministic.
SWITCH = "dfw01-inb-sw-lea-03"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _mock_driver(**methods: Any) -> Any:
    """Patch ``server.get_driver`` to return an AsyncMock with scripted methods."""
    drv = AsyncMock()
    for name, value in methods.items():
        getattr(drv, name).return_value = value
    return patch("mcp_network.server.get_driver", return_value=drv)


# ---------------------------------------------------------------------------
# get_mac_table — limit / count_only / total / truncated / MAC normalization
# ---------------------------------------------------------------------------


def test_mac_table_limit_and_total() -> None:
    entries = [
        {"id": str(i), "mac": f"aa:bb:cc:00:00:{i:02x}", "interface": "swp1", "vlan": 1}
        for i in range(5)
    ]
    with _mock_driver(mac_table=entries):
        res = _run(server.get_mac_table(switch=SWITCH, limit=2))
    assert res["total"] == 5
    assert len(res["entries"]) == 2
    assert res["truncated"] is True


def test_mac_table_not_truncated_under_limit() -> None:
    entries = [{"mac": "aa:bb:cc:00:00:01", "interface": "swp1", "vlan": 1}]
    with _mock_driver(mac_table=entries):
        res = _run(server.get_mac_table(switch=SWITCH, limit=200))
    assert res["total"] == 1
    assert res["truncated"] is False
    assert len(res["entries"]) == 1


def test_mac_table_count_only_omits_entries() -> None:
    entries = [{"mac": "aa:bb:cc:00:00:01", "interface": "swp1", "vlan": 1}] * 3
    with _mock_driver(mac_table=entries):
        res = _run(server.get_mac_table(switch=SWITCH, count_only=True))
    assert res["total"] == 3
    assert res["count_only"] is True
    assert "entries" not in res


def test_mac_table_normalizes_filter_forms() -> None:
    entries = [
        {"mac": "9c:63:c0:26:ef:ec", "interface": "swp14s1", "vlan": 1229},
        {"mac": "11:22:33:44:55:66", "interface": "swp2", "vlan": 1},
    ]
    # bare-hex, dotted, and upper-colon all normalize to the same canonical MAC,
    # which used to silently match nothing.
    for needle in ("9c63c026efec", "9C63.C026.EFEC", "9C:63:C0:26:EF:EC"):
        with _mock_driver(mac_table=entries):
            res = _run(server.get_mac_table(switch=SWITCH, mac=needle))
        assert [e["mac"] for e in res["entries"]] == ["9c:63:c0:26:ef:ec"], needle


# ---------------------------------------------------------------------------
# get_wjh — limit
# ---------------------------------------------------------------------------


def test_wjh_limit() -> None:
    entries = [{"id": str(i), "reason": "ACL deny", "ingress-port": "swp1"} for i in range(150)]
    with _mock_driver(wjh=entries):
        res = _run(server.get_wjh(switch=SWITCH, limit=100))
    assert res["total"] == 150
    assert len(res["entries"]) == 100
    assert res["truncated"] is True


# ---------------------------------------------------------------------------
# get_port_status — brief all-ports projection
# ---------------------------------------------------------------------------


def test_port_status_brief_projects_nested_link() -> None:
    ports = [
        {
            "name": "swp29",
            "type": "swp",
            "link": {"admin-status": "up", "oper-status": "up", "speed": "400G", "mtu": 9216},
            "ip": {"addr": "x"},
        }
    ]
    with _mock_driver(interfaces_brief=ports):
        res = _run(server.get_port_status(switch=SWITCH, brief=True))
    assert res["ports"] == [
        {
            "name": "swp29",
            "admin": "up",
            "oper": "up",
            "speed": "400G",
            "classification": "uplink",
        }
    ]


def test_port_status_brief_handles_flat_fields() -> None:
    ports = [{"name": "swp1", "oper-state": "up", "speed": "100G"}]
    with _mock_driver(interfaces_brief=ports):
        res = _run(server.get_port_status(switch=SWITCH, brief=True))
    port = res["ports"][0]
    assert port["name"] == "swp1"
    assert port["oper"] == "up"
    assert port["speed"] == "100G"
    assert port["classification"] == "downlink"


def test_port_status_full_returns_raw() -> None:
    ports = [{"name": "swp1", "link": {"oper-status": "down"}, "extra": {"x": 1}}]
    with _mock_driver(interfaces_brief=ports):
        res = _run(server.get_port_status(switch=SWITCH, brief=False))
    assert res["ports"] == ports


def test_port_status_single_port_unaffected_by_brief() -> None:
    payload = {"link": {"oper-status": "up", "speed": "400G"}}
    with _mock_driver(interface=payload):
        res = _run(server.get_port_status(switch=SWITCH, port="swp14s1", brief=True))
    assert res["port"] == "swp14s1"
    assert res["data"] == payload


# ---------------------------------------------------------------------------
# get_lldp_neighbors — brief projection
# ---------------------------------------------------------------------------


def test_lldp_brief_projects_nested_nvue() -> None:
    raw = [
        {
            "name": "swp29",
            "lldp": {"0": {"chassis": {"system-name": "spine-01"}, "port": {"name": "swp1"}}},
        }
    ]
    with _mock_driver(lldp=raw):
        res = _run(server.get_lldp_neighbors(switch=SWITCH, brief=True))
    assert res["neighbors"] == [
        {"local-port": "swp29", "remote-system": "spine-01", "remote-port": "swp1"}
    ]


def test_lldp_full_returns_raw() -> None:
    raw = [{"name": "swp29", "lldp": {"0": {"chassis": {"system-name": "x"}}}}]
    with _mock_driver(lldp=raw):
        res = _run(server.get_lldp_neighbors(switch=SWITCH, brief=False))
    assert res["neighbors"] == raw


# ---------------------------------------------------------------------------
# get_bgp_neighbors — brief projection
# ---------------------------------------------------------------------------


def test_bgp_brief_projects_summary() -> None:
    data = {
        "swp51": {
            "remote-as": 65199,
            "state": "established",
            "up-time": 2136000,
            "afi-safi": {"ipv4-unicast": {"rx-prefix": 8, "tx-prefix": 13}},
            "capabilities": {"route-refresh": "on"},
        }
    }
    with _mock_driver(bgp_summary=data):
        res = _run(server.get_bgp_neighbors(switch=SWITCH, brief=True))
    assert res["data"]["swp51"] == {
        "remote-as": 65199,
        "state": "established",
        "uptime": 2136000,
        "prefixes": {"ipv4-unicast": {"rx": 8, "tx": 13}},
    }


def test_bgp_full_returns_raw() -> None:
    data = {"swp51": {"remote-as": 65199, "capabilities": {"route-refresh": "on"}}}
    with _mock_driver(bgp_summary=data):
        res = _run(server.get_bgp_neighbors(switch=SWITCH, brief=False))
    assert res["data"] == data


# ---------------------------------------------------------------------------
# Projection helpers (edge cases)
# ---------------------------------------------------------------------------


def test_brief_lldp_idempotent_on_clean_shape() -> None:
    clean = {"local-port": "swp1", "remote-system": "spi", "remote-port": "swp2"}
    assert server._brief_lldp_neighbor(clean) == clean


def test_brief_bgp_handles_flat_prefix_counts() -> None:
    data = {"swp1": {"remote-as": 1, "state": "established", "pfx-rcvd": 5, "pfx-sent": 9}}
    assert server._brief_bgp_neighbors(data)["swp1"]["prefixes"] == {"rx": 5, "tx": 9}


def test_scalarize_collapses_single_key_enum() -> None:
    assert server._scalarize({"up": {}}) == "up"
    assert server._scalarize("up") == "up"


# ---------------------------------------------------------------------------
# Human-output formatters (bypassed in --json mode; exercised directly)
# ---------------------------------------------------------------------------


def test_fmt_switches_renders_rows() -> None:
    out = server._fmt_switches(
        {
            "site": "ori",
            "switches": [
                {
                    "name": "lea-03",
                    "mgmt_ip": "1.2.3.4",
                    "role": "leaf",
                    "model": "SN5600",
                    "reachable": True,
                }
            ],
        }
    )
    assert "site: ori" in out
    assert "lea-03" in out and "SN5600" in out


def test_fmt_mac_table_count_only() -> None:
    out = server._fmt_mac_table({"switch": SWITCH, "total": 42, "count_only": True})
    assert "42 match" in out


def test_fmt_mac_table_truncated_header() -> None:
    out = server._fmt_mac_table(
        {
            "switch": SWITCH,
            "total": 5,
            "truncated": True,
            "entries": [{"mac": "a", "interface": "swp1", "vlan": 1, "age": 2}],
        }
    )
    assert "mac-table: 5" in out
    assert "showing 1" in out


def test_fmt_lldp_renders_arrow() -> None:
    out = server._fmt_lldp(
        {
            "switch": SWITCH,
            "neighbors": [
                {"local-port": "swp29", "remote-system": "spi-01", "remote-port": "swp1"}
            ],
        }
    )
    assert "swp29" in out and "-> spi-01 (swp1)" in out


def test_fmt_bgp_renders_state() -> None:
    out = server._fmt_bgp(
        {
            "switch": SWITCH,
            "data": {
                "swp51": {
                    "remote-as": 65199,
                    "state": "established",
                    "uptime": 5,
                    "prefixes": {"ipv4-unicast": {"rx": 8, "tx": 13}},
                }
            },
        }
    )
    assert "swp51" in out and "as=65199" in out and "state=established" in out


def test_fmt_logs_renders_entries() -> None:
    out = server._fmt_logs(
        {
            "switch": SWITCH,
            "entries": [
                {"timestamp": "t", "priority": "err", "identifier": "bgpd", "message": "down"}
            ],
        }
    )
    assert "bgpd: down" in out


def test_fmt_wjh_renders_reason() -> None:
    out = server._fmt_wjh(
        {"switch": SWITCH, "total": 1, "entries": [{"reason": "ACL deny", "ingress-port": "swp1"}]}
    )
    assert "ACL deny" in out and "ingress=swp1" in out
