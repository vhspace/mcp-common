"""Config drift detection and comparison for MAAS machines.

This module provides utilities for comparing machine configurations to detect
drift in NICs, storage, and BIOS settings.
"""

from typing import Any


def compare_nics(machine1: dict[str, Any], machine2: dict[str, Any]) -> dict[str, Any]:
    """
    Compare NIC configurations between two machines.

    Args:
        machine1: First machine dict (must include interfaces)
        machine2: Second machine dict (must include interfaces)

    Returns:
        Dict with comparison results:
        - matches: List of matching interfaces
        - only_in_machine1: Interfaces only in machine1
        - only_in_machine2: Interfaces only in machine2
        - differences: Interfaces with differences
    """
    interfaces1 = machine1.get("interfaces", [])
    interfaces2 = machine2.get("interfaces", [])

    if not isinstance(interfaces1, list):
        interfaces1 = [interfaces1] if interfaces1 else []
    if not isinstance(interfaces2, list):
        interfaces2 = [interfaces2] if interfaces2 else []

    # Index by MAC address (most reliable identifier)
    by_mac1 = {iface.get("mac_address"): iface for iface in interfaces1 if iface.get("mac_address")}
    by_mac2 = {iface.get("mac_address"): iface for iface in interfaces2 if iface.get("mac_address")}

    matches = []
    only_in_machine1 = []
    only_in_machine2 = []
    differences = []

    # Find matches and differences
    all_macs = set(by_mac1.keys()) | set(by_mac2.keys())

    for mac in all_macs:
        iface1 = by_mac1.get(mac)
        iface2 = by_mac2.get(mac)

        if iface1 and iface2:
            # Compare interface properties
            diff = _compare_interface_properties(iface1, iface2)
            if diff:
                differences.append(
                    {
                        "mac_address": mac,
                        "machine1": iface1,
                        "machine2": iface2,
                        "differences": diff,
                    }
                )
            else:
                matches.append({"mac_address": mac, "interface": iface1})
        elif iface1:
            only_in_machine1.append(iface1)
        elif iface2:
            only_in_machine2.append(iface2)

    return {
        "matches": matches,
        "only_in_machine1": only_in_machine1,
        "only_in_machine2": only_in_machine2,
        "differences": differences,
    }


def compare_storage(machine1: dict[str, Any], machine2: dict[str, Any]) -> dict[str, Any]:
    """
    Compare storage/block device configurations between two machines.

    Args:
        machine1: First machine dict (must include block_devices)
        machine2: Second machine dict (must include block_devices)

    Returns:
        Dict with comparison results
    """
    devices1 = machine1.get("block_devices", [])
    devices2 = machine2.get("block_devices", [])

    if not isinstance(devices1, list):
        devices1 = [devices1] if devices1 else []
    if not isinstance(devices2, list):
        devices2 = [devices2] if devices2 else []

    # Index by serial number or id_path (most reliable identifiers)
    def get_key(device: dict[str, Any]) -> str | None:
        return device.get("serial") or device.get("id_path") or device.get("name")

    by_key1 = {get_key(d): d for d in devices1 if get_key(d)}
    by_key2 = {get_key(d): d for d in devices2 if get_key(d)}

    matches = []
    only_in_machine1 = []
    only_in_machine2 = []
    differences = []

    all_keys = set(by_key1.keys()) | set(by_key2.keys())

    for key in all_keys:
        dev1 = by_key1.get(key)
        dev2 = by_key2.get(key)

        if dev1 and dev2:
            diff = _compare_storage_properties(dev1, dev2)
            if diff:
                differences.append(
                    {
                        "key": key,
                        "machine1": dev1,
                        "machine2": dev2,
                        "differences": diff,
                    }
                )
            else:
                matches.append({"key": key, "device": dev1})
        elif dev1:
            only_in_machine1.append(dev1)
        elif dev2:
            only_in_machine2.append(dev2)

    return {
        "matches": matches,
        "only_in_machine1": only_in_machine1,
        "only_in_machine2": only_in_machine2,
        "differences": differences,
    }


def compare_bios(machine1: dict[str, Any], machine2: dict[str, Any]) -> dict[str, Any]:
    """
    Compare BIOS settings between two machines.

    Args:
        machine1: First machine dict
        machine2: Second machine dict

    Returns:
        Dict with comparison results
    """
    bios1 = machine1.get("bios_settings", {}) or {}
    bios2 = machine2.get("bios_settings", {}) or {}

    if not isinstance(bios1, dict):
        bios1 = {}
    if not isinstance(bios2, dict):
        bios2 = {}

    differences = []
    matches = []

    all_keys = set(bios1.keys()) | set(bios2.keys())

    for key in all_keys:
        val1 = bios1.get(key)
        val2 = bios2.get(key)

        if val1 != val2:
            differences.append(
                {
                    "setting": key,
                    "machine1": val1,
                    "machine2": val2,
                }
            )
        else:
            matches.append({"setting": key, "value": val1})

    return {
        "matches": matches,
        "only_in_machine1": {k: v for k, v in bios1.items() if k not in bios2},
        "only_in_machine2": {k: v for k, v in bios2.items() if k not in bios1},
        "differences": differences,
    }


def _compare_interface_properties(iface1: dict[str, Any], iface2: dict[str, Any]) -> dict[str, Any]:
    """Compare properties of two interfaces and return differences."""
    differences: dict[str, Any] = {}

    # Key properties to compare
    props = ["name", "type", "vlan", "links", "tags", "enabled", "parents", "children"]

    for prop in props:
        val1 = iface1.get(prop)
        val2 = iface2.get(prop)

        if val1 != val2:
            differences[prop] = {"machine1": val1, "machine2": val2}

    # Compare IP addresses
    ip1 = iface1.get("ip_addresses", [])
    ip2 = iface2.get("ip_addresses", [])
    if ip1 != ip2:
        differences["ip_addresses"] = {"machine1": ip1, "machine2": ip2}

    return differences


def _compare_storage_properties(dev1: dict[str, Any], dev2: dict[str, Any]) -> dict[str, Any]:
    """Compare properties of two storage devices and return differences."""
    differences: dict[str, Any] = {}

    # Key properties to compare
    props = ["name", "size", "model", "serial", "block_size", "id_path", "type"]

    for prop in props:
        val1 = dev1.get(prop)
        val2 = dev2.get(prop)

        if val1 != val2:
            differences[prop] = {"machine1": val1, "machine2": val2}

    # Compare partitions if present
    parts1 = dev1.get("partitions", [])
    parts2 = dev2.get("partitions", [])
    if parts1 != parts2:
        differences["partitions"] = {"machine1": parts1, "machine2": parts2}

    return differences
