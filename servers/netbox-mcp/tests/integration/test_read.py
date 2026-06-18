"""Read-path integration tests against the seeded NetBox simulator.

These drive the REAL netbox-mcp tool functions (the same callables the MCP
server and CLI use) against a live NetBox booted in Docker and seeded by
``seed.py``. They are marked ``integration`` and so are excluded from the fast
unit gate.
"""

from __future__ import annotations

import pytest

from netbox_mcp.server import (
    netbox_get_objects,
    netbox_get_objects_by_ids,
    netbox_lookup_device,
    netbox_oob_summary,
    netbox_search_objects,
)

pytestmark = pytest.mark.integration


@pytest.mark.anyio
async def test_search_finds_seeded_devices_and_sites(netbox_client: object) -> None:
    """Global search returns seeded devices and sites by keyword."""
    devices = await netbox_search_objects(query="sim-gpu", object_types=["dcim.device"])
    names = {d["name"] for d in devices["dcim.device"]}
    assert {"sim-gpu-01", "sim-gpu-02"} <= names

    sites = await netbox_search_objects(query="ORI", object_types=["dcim.site"])
    site_names = {s["name"] for s in sites["dcim.site"]}
    assert "ORI-TX" in site_names


def test_lookup_device_by_hostname(netbox_client: object) -> None:
    """lookup-device resolves a seeded hostname and enriches its IP fields."""
    result = netbox_lookup_device(hostname="sim-gpu-01")

    assert result["count"] >= 1
    assert result["query"] == "sim-gpu-01"
    device = next(d for d in result["results"] if d["name"] == "sim-gpu-01")
    assert device["primary_ip4_address"] == "10.10.0.11"
    assert device["oob_ip_address"] == "192.168.196.11"


def test_lookup_device_by_provider_machine_id(netbox_client: object) -> None:
    """lookup-device falls back to the Provider_Machine_ID custom field."""
    result = netbox_lookup_device(hostname="GPU-SIM-001")

    assert result["count"] >= 1
    device = result["results"][0]
    assert device["name"] == "sim-gpu-01"
    assert device["provider_machine_id"] == "GPU-SIM-001"


def test_lookup_device_by_ip(netbox_client: object) -> None:
    """lookup-device reverse-resolves an IP address to its device (IPAM fallback)."""
    result = netbox_lookup_device(hostname="10.10.0.11")

    assert result["count"] >= 1
    assert any(d["name"] == "sim-gpu-01" for d in result["results"])


def test_pagination_over_devices(netbox_client: object) -> None:
    """limit/offset page over devices without overlap; count is the full total."""
    page1 = netbox_get_objects(
        object_type="dcim.device", filters={}, limit=2, offset=0, ordering="name"
    )
    page2 = netbox_get_objects(
        object_type="dcim.device", filters={}, limit=2, offset=2, ordering="name"
    )

    assert page1["count"] >= 6
    assert len(page1["results"]) == 2
    assert len(page2["results"]) == 2
    ids1 = {d["id"] for d in page1["results"]}
    ids2 = {d["id"] for d in page2["results"]}
    assert ids1.isdisjoint(ids2)


def test_ordering_by_name(netbox_client: object) -> None:
    """Ascending and descending ordering are honored by the API."""
    asc = netbox_get_objects(object_type="dcim.device", filters={}, limit=100, ordering="name")
    desc = netbox_get_objects(object_type="dcim.device", filters={}, limit=100, ordering="-name")

    asc_names = [d["name"] for d in asc["results"]]
    desc_names = [d["name"] for d in desc["results"]]
    assert asc_names == sorted(asc_names)
    assert asc_names == list(reversed(desc_names))


def test_filter_devices_by_role(netbox_client: object) -> None:
    """A key=value filter narrows results to the four seeded gpu devices."""
    total = netbox_get_objects(object_type="dcim.device", filters={}, limit=100)
    gpus = netbox_get_objects(object_type="dcim.device", filters={"role": "gpu"}, limit=100)

    assert total["count"] >= 6
    assert gpus["count"] == 4
    assert gpus["count"] < total["count"]
    for device in gpus["results"]:
        role = device.get("role") or device.get("device_role")
        role_slug = role.get("slug") if isinstance(role, dict) else role
        assert role_slug == "gpu"


def test_lookup_device_structured_shape(netbox_client: object) -> None:
    """lookup-device returns the documented structured shape (count/results/query)."""
    result = netbox_lookup_device(hostname="sim-gpu-01")

    assert {"count", "results", "query"} <= result.keys()
    assert isinstance(result["count"], int)
    assert isinstance(result["results"], list)
    assert result["query"] == "sim-gpu-01"


def test_oob_summary_structured_output(netbox_client: object) -> None:
    """oob-summary returns a typed DeviceOOBSummary with cross-MCP IP fields."""
    summary = netbox_oob_summary(hostname="sim-gpu-01")

    assert summary.name == "sim-gpu-01"
    assert summary.oob_ip == "192.168.196.11"
    assert summary.primary_ip4 == "10.10.0.11"
    assert summary.provider_machine_id == "GPU-SIM-001"


def test_get_objects_by_ids_roundtrip(netbox_client: object) -> None:
    """get-objects-by-ids fetches multiple devices by numeric id in one call."""
    devices = netbox_get_objects(object_type="dcim.device", filters={}, limit=100)
    ids = sorted(d["id"] for d in devices["results"])[:2]
    assert len(ids) == 2

    fetched = netbox_get_objects_by_ids(object_type="dcim.device", ids=ids)
    assert fetched["count"] == 2
    assert {d["id"] for d in fetched["results"]} == set(ids)
