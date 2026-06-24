"""Integration tests — end-to-end via the FastMCP client protocol."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client
from mcp_common.testing import assert_tool_exists, assert_tool_success

EXPECTED_TOOLS = {
    "list_sites",
    "list_switches",
    "get_system_info",
    "get_port_status",
    "get_port_counters",
    "get_lldp_neighbors",
    "get_bgp_neighbors",
    "get_mac_table",
    "find_port_for_mac",
    "find_port_for_node",
}


@pytest.mark.anyio
async def test_tool_discovery(client: Client) -> None:
    for name in sorted(EXPECTED_TOOLS):
        await assert_tool_exists(client, name)


@pytest.mark.anyio
async def test_list_sites_returns_ori(client: Client) -> None:
    result = await assert_tool_success(client, "list_sites")
    content = _as_dict(result)
    assert content["default"] == "ori"
    sites = {s["site"] for s in content["sites"]}
    assert "ori" in sites


@pytest.mark.anyio
async def test_list_switches_has_six(client: Client) -> None:
    result = await assert_tool_success(client, "list_switches", {"site": "ori"})
    content = _as_dict(result)
    assert content["site"] == "ori"
    names = {s["name"] for s in content["switches"]}
    assert "dfw01-inb-sw-lea-03" in names
    assert len(names) == 6


@pytest.mark.anyio
async def test_get_system_info_brief_default(client: Client) -> None:
    """brief=True (default) returns only key fields from system_info."""
    raw = {
        "hostname": "dfw01-inb-sw-lea-03",
        "health": "ok",
        "uptime": "3d 12h",
        "os-version": "Cumulus Linux 5.15.0",
        "model": "SN5600",
        "platform": "NVIDIA",
        "some-internal-blob": {"nested": "data"},
        "extra-field": 42,
    }

    class FakeDriver:
        async def system_info(self) -> dict[str, Any]:
            return raw

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client, "get_system_info", {"switch": "dfw01-inb-sw-lea-03"}
        )
    content = _as_dict(result)
    assert content["switch"] == "dfw01-inb-sw-lea-03"
    assert content["data"]["hostname"] == "dfw01-inb-sw-lea-03"
    assert content["data"]["health"] == "ok"
    assert "some-internal-blob" not in content["data"]
    assert "extra-field" not in content["data"]


@pytest.mark.anyio
async def test_get_system_info_full(client: Client) -> None:
    """brief=False returns the complete raw blob."""
    raw = {
        "hostname": "dfw01-inb-sw-lea-03",
        "health": "ok",
        "some-internal-blob": {"nested": "data"},
    }

    class FakeDriver:
        async def system_info(self) -> dict[str, Any]:
            return raw

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client, "get_system_info", {"switch": "dfw01-inb-sw-lea-03", "brief": False}
        )
    content = _as_dict(result)
    assert content["data"] == raw


@pytest.mark.anyio
async def test_get_port_status_uses_mocked_driver(client: Client) -> None:
    """End-to-end with a mocked Cumulus driver to avoid live SSH."""
    payload = {"link": {"admin-status": "up", "oper-status": "up", "speed": "400G"}}

    class FakeDriver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def interface(self, port: str) -> dict[str, Any]:
            self.calls.append(("interface", port))
            return payload

    fake = FakeDriver()
    with patch("mcp_network.server.get_driver", return_value=fake):
        result = await assert_tool_success(
            client,
            "get_port_status",
            {"switch": "dfw01-inb-sw-lea-03", "port": "swp14s1"},
        )
    content = _as_dict(result)
    assert content["site"] == "ori"
    assert content["switch"] == "dfw01-inb-sw-lea-03"
    assert content["port"] == "swp14s1"
    assert content["classification"] == "downlink"
    assert content["data"] == payload
    assert fake.calls == [("interface", "swp14s1")]


@pytest.mark.anyio
async def test_uplink_ports_classified_correctly(client: Client) -> None:
    payload = {"link": {"oper-status": "up"}}

    class FakeDriver:
        async def interface(self, port: str) -> dict[str, Any]:
            return payload

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client,
            "get_port_status",
            {"switch": "dfw01-inb-sw-lea-03", "port": "swp29"},
        )
    content = _as_dict(result)
    assert content["classification"] == "uplink"


@pytest.mark.anyio
async def test_find_port_for_mac_direct_vs_indirect(client: Client) -> None:
    """Simulates the real -078 topology: one leaf sees swp14s1, others see bond1."""
    target_mac = "9c:63:c0:26:ef:ec"

    def entries_for(sw_name: str) -> list[dict[str, Any]]:
        if sw_name == "dfw01-inb-sw-lea-03":
            return [{"id": "42", "mac": target_mac, "interface": "swp14s1", "vlan": 1229}]
        if sw_name.startswith("dfw01-inb-sw-lea-"):
            return [{"id": "99", "mac": target_mac, "interface": "bond1", "vlan": 1229}]
        return []

    class FakeDriver:
        def __init__(self, sw_name: str) -> None:
            self.sw_name = sw_name

        @asynccontextmanager
        async def session(self) -> AsyncIterator[None]:
            yield

        async def mac_table(self) -> list[dict[str, Any]]:
            return entries_for(self.sw_name)

    def driver_for(site_cfg: Any, switch: Any) -> FakeDriver:
        return FakeDriver(switch.name)

    with patch("mcp_network.server.get_driver", side_effect=driver_for):
        result = await assert_tool_success(client, "find_port_for_mac", {"mac": target_mac})
    content = _as_dict(result)
    assert content["mac"] == target_mac
    # exactly one direct hit (lea-03 swp14s1)
    assert len(content["direct"]) == 1
    direct = content["direct"][0]
    assert direct["switch"] == "dfw01-inb-sw-lea-03"
    assert direct["port"] == "swp14s1"
    # other 3 leaves seen via bond (spi-01 is unreachable -> skipped, spi-02 returns [])
    indirect_switches = {r["switch"] for r in content["indirect"]}
    assert indirect_switches == {
        "dfw01-inb-sw-lea-01",
        "dfw01-inb-sw-lea-02",
        "dfw01-inb-sw-lea-04",
    }


@pytest.mark.anyio
async def test_get_port_counters_with_mocked_driver(client: Client) -> None:
    counters = {
        "in-bytes": 123456,
        "out-bytes": 654321,
        "in-errors": 0,
        "out-errors": 0,
        "in-drops": 2,
    }

    class FakeDriver:
        @asynccontextmanager
        async def session(self) -> AsyncIterator[None]:
            yield

        async def interface_counters(self, port: str) -> dict[str, Any]:
            return counters

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client,
            "get_port_counters",
            {"switch": "dfw01-inb-sw-lea-03", "port": "swp14s1"},
        )
    content = _as_dict(result)
    assert content["site"] == "ori"
    assert content["switch"] == "dfw01-inb-sw-lea-03"
    assert content["port"] == "swp14s1"
    assert content["classification"] == "downlink"
    assert content["data"] == counters


@pytest.mark.anyio
async def test_get_lldp_neighbors_with_mocked_driver(client: Client) -> None:
    neighbors = [
        {"local-port": "swp29", "remote-system": "dfw01-inb-sw-spi-01", "remote-port": "swp1"},
        {"local-port": "swp30", "remote-system": "dfw01-inb-sw-spi-02", "remote-port": "swp1"},
    ]

    class FakeDriver:
        @asynccontextmanager
        async def session(self) -> AsyncIterator[None]:
            yield

        async def lldp(self) -> list[dict[str, Any]]:
            return neighbors

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client,
            "get_lldp_neighbors",
            {"switch": "dfw01-inb-sw-lea-01"},
        )
    content = _as_dict(result)
    assert content["site"] == "ori"
    assert content["switch"] == "dfw01-inb-sw-lea-01"
    assert content["neighbors"] == neighbors


@pytest.mark.anyio
async def test_get_bgp_neighbors_with_mocked_driver(client: Client) -> None:
    bgp_data = {
        "ipv4-unicast": {
            "peer-count": 2,
            "peers": {
                "10.0.0.1": {"state": "established", "prefixes-received": 10},
                "10.0.0.2": {"state": "established", "prefixes-received": 8},
            },
        }
    }

    class FakeDriver:
        @asynccontextmanager
        async def session(self) -> AsyncIterator[None]:
            yield

        async def bgp_summary(self) -> dict[str, Any]:
            return bgp_data

    with patch("mcp_network.server.get_driver", return_value=FakeDriver()):
        result = await assert_tool_success(
            client,
            "get_bgp_neighbors",
            {"switch": "dfw01-inb-sw-spi-01"},
        )
    content = _as_dict(result)
    assert content["site"] == "ori"
    assert content["switch"] == "dfw01-inb-sw-spi-01"
    assert content["data"] == bgp_data


@pytest.mark.anyio
async def test_find_port_for_node_with_mocked_netbox(client: Client) -> None:
    """Mocks netbox-cli NIC lookup and switch drivers to test full flow."""
    target_mac = "aa:bb:cc:dd:ee:01"
    nics = [{"name": "eth0", "mac": target_mac, "type": "1000base-t"}]

    class FakeDriver:
        def __init__(self, sw_name: str) -> None:
            self.sw_name = sw_name

        @asynccontextmanager
        async def session(self) -> AsyncIterator[None]:
            yield

        async def mac_table(self) -> list[dict[str, Any]]:
            if self.sw_name == "dfw01-inb-sw-lea-02":
                return [{"mac": target_mac, "interface": "swp10", "vlan": 1229}]
            return []

    def driver_for(site_cfg: Any, switch: Any) -> FakeDriver:
        return FakeDriver(switch.name)

    with (
        patch("mcp_network.server.get_node_nic_macs", return_value=nics),
        patch("mcp_network.server.get_driver", side_effect=driver_for),
    ):
        result = await assert_tool_success(client, "find_port_for_node", {"node": "test-node-001"})
    content = _as_dict(result)
    assert content["node"] == "test-node-001"
    assert content["site"] == "ori"
    assert len(content["nics"]) == 1
    nic = content["nics"][0]
    assert nic["nic"] == "eth0"
    assert nic["mac"] == target_mac
    assert len(nic["direct"]) == 1
    assert nic["direct"][0]["switch"] == "dfw01-inb-sw-lea-02"
    assert nic["direct"][0]["port"] == "swp10"


@pytest.mark.anyio
async def test_unknown_switch_reports_clear_error(client: Client) -> None:
    async with client:
        with pytest.raises(Exception) as exc:
            await client.call_tool("get_port_status", {"switch": "not-a-switch"})
    assert "not-a-switch" in str(exc.value)


def _as_dict(result: Any) -> dict[str, Any]:
    """Extract structured content from a FastMCP tool result."""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content  # type: ignore[no-any-return]
    if hasattr(result, "data"):
        return result.data  # type: ignore[no-any-return]
    if hasattr(result, "content"):
        text = result.content[0].text
        return json.loads(text)  # type: ignore[no-any-return]
    return result  # type: ignore[return-value]
