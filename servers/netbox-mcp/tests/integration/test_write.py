"""Write round-trip integration test (backs #42).

A real PATCH against the local sim is safe — there is no Cloudflare in front of
it, unlike production NetBox (which blocks writes from non-VPN clients). The
``netbox_client`` fixture disables the VPN monitor and clears
``MCP_ENFORCE_READONLY`` so this exercises the genuine write path.
"""

from __future__ import annotations

import pytest

from netbox_mcp.server import (
    netbox_get_changelogs,
    netbox_get_object_by_id,
    netbox_lookup_device,
    netbox_update_device,
)

pytestmark = pytest.mark.integration

WRITE_TARGET = "sim-gpu-04"


def _status_value(status: object) -> object:
    return status["value"] if isinstance(status, dict) else status


def test_update_device_status_round_trip(netbox_client: object) -> None:
    """Flip a seeded device active -> offline, read it back, then restore it."""
    before = netbox_lookup_device(hostname=WRITE_TARGET)
    device = before["results"][0]
    device_id = device["id"]
    assert _status_value(device["status"]) == "active"

    try:
        updated = netbox_update_device(device=WRITE_TARGET, status="offline")
        assert updated["device"]["status"]["value"] == "offline"
        assert any("offline" in change for change in updated["changes"])

        readback = netbox_get_object_by_id(
            object_type="dcim.device", object_id=device_id, fields=["id", "name", "status"]
        )
        assert _status_value(readback["status"]) == "offline"

        # The PATCH above must have produced an ``update`` changelog entry that
        # netbox_get_changelogs can retrieve (exercises core/object-changes).
        changelogs = netbox_get_changelogs(
            filters={"changed_object_id": device_id, "action": "update"}
        )
        assert changelogs["count"] >= 1
    finally:
        restored = netbox_update_device(device=WRITE_TARGET, status="active")
        assert restored["device"]["status"]["value"] == "active"
