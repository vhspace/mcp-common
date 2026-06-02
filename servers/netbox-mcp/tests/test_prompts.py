"""Tests for MCP prompt templates."""

from netbox_mcp.server import (
    audit_site,
    find_available_ips,
    inventory_report,
    investigate_device,
    troubleshoot_connectivity,
)


def test_investigate_device_includes_hostname():
    result = investigate_device("gpu-node-01")
    assert "gpu-node-01" in result
    assert "netbox_search_objects" in result
    assert "netbox_get_object_by_id" in result


def test_audit_site_includes_site_name():
    result = audit_site("DC1")
    assert "DC1" in result
    assert "dcim.device" in result
    assert "dcim.rack" in result
    assert "ipam.vlan" in result


def test_troubleshoot_connectivity_includes_both_devices():
    result = troubleshoot_connectivity("switch-01", "router-01")
    assert "switch-01" in result
    assert "router-01" in result
    assert "netbox_lookup_device" in result
    assert "connected_endpoints" in result


def test_inventory_report_includes_site():
    result = inventory_report("ORI-TX")
    assert "ORI-TX" in result
    assert "device_role" in result
    assert "device_type" in result


def test_find_available_ips_includes_prefix():
    result = find_available_ips("10.0.0.0/24")
    assert "10.0.0.0/24" in result
    assert "ipam.ipaddress" in result
    assert "ipam.prefix" in result
