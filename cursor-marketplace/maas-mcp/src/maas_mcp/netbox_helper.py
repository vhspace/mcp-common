"""NetBox cross-reference utilities for MAAS machines.

This module provides standalone utility functions for linking MAAS machines with
NetBox devices/VMs using Provider_Machine_ID and fuzzy matching. These functions
operate on plain dicts and are designed to be called from external code or tests.

Use maas_get_machine(include=['interfaces']) to retrieve machine data suitable
for cross-referencing, then pass it to these helpers alongside NetBox device data.

The ``extract_network_profile`` function converts raw MAAS ``interface_set``
data into a portable profile dict suitable for replication across instances.
"""

from __future__ import annotations

from typing import Any


def find_device_by_provider_id(
    provider_id: str, netbox_devices: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """
    Find NetBox device/VM by Provider_Machine_ID.

    Args:
        provider_id: Provider_Machine_ID to search for
        netbox_devices: List of NetBox device/VM objects

    Returns:
        Matching device/VM dict or None
    """
    for device in netbox_devices:
        custom_fields = device.get("custom_fields", {}) or {}
        provider_machine_id = custom_fields.get("Provider_Machine_ID")
        if provider_machine_id and str(provider_machine_id).lower() == str(provider_id).lower():
            return device
    return None


def fuzzy_match_machine(
    maas_machine: dict[str, Any], netbox_devices: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Fuzzy match MAAS machine to NetBox device using multiple strategies.

    Matching strategies (in order):
    1. Hostname matches Provider_Machine_ID
    2. MAC address matching
    3. IP address matching

    Args:
        maas_machine: MAAS machine dict
        netbox_devices: List of NetBox device/VM objects

    Returns:
        Dict with:
        - match: Best matching device or None
        - confidence: Match confidence (high/medium/low)
        - method: Matching method used
        - warnings: List of warnings
    """
    hostname = maas_machine.get("hostname", "").lower()
    system_id = maas_machine.get("system_id", "")

    # Get MAC addresses from interfaces
    mac_addresses = []
    interfaces = maas_machine.get("interfaces", [])
    if isinstance(interfaces, list):
        for iface in interfaces:
            mac = iface.get("mac_address")
            if mac:
                mac_addresses.append(mac.lower())
    elif interfaces:
        mac = interfaces.get("mac_address")
        if mac:
            mac_addresses.append(mac.lower())

    # Get IP addresses from interfaces
    ip_addresses = []
    if isinstance(interfaces, list):
        for iface in interfaces:
            ips = iface.get("ip_addresses", [])
            if isinstance(ips, list):
                for ip_obj in ips:
                    if isinstance(ip_obj, dict):
                        ip = ip_obj.get("ip")
                    else:
                        ip = str(ip_obj)
                    if ip:
                        ip_addresses.append(ip)
    elif interfaces:
        ips = interfaces.get("ip_addresses", [])
        if isinstance(ips, list):
            ip_addresses.extend([str(ip) for ip in ips if ip])

    warnings = []
    best_match = None
    confidence = "none"
    method = None

    # Strategy 1: Hostname matches Provider_Machine_ID
    for device in netbox_devices:
        custom_fields = device.get("custom_fields", {}) or {}
        provider_id = custom_fields.get("Provider_Machine_ID")
        if provider_id and str(provider_id).lower() == hostname:
            best_match = device
            confidence = "high"
            method = "hostname_provider_id"
            break

    # Strategy 2: MAC address matching
    if not best_match:
        for device in netbox_devices:
            device_interfaces = device.get("interfaces", [])
            if not isinstance(device_interfaces, list):
                device_interfaces = [device_interfaces] if device_interfaces else []

            for dev_iface in device_interfaces:
                dev_mac = dev_iface.get("mac_address")
                if dev_mac and dev_mac.lower() in mac_addresses:
                    if best_match and best_match != device:
                        warnings.append(f"Multiple devices match MAC {dev_mac}, using first match")
                    best_match = device
                    confidence = "medium"
                    method = "mac_address"
                    break
            if best_match:
                break

    # Strategy 3: IP address matching
    if not best_match:
        for device in netbox_devices:
            device_interfaces = device.get("interfaces", [])
            if not isinstance(device_interfaces, list):
                device_interfaces = [device_interfaces] if device_interfaces else []

            for dev_iface in device_interfaces:
                dev_ips = dev_iface.get("ip_addresses", [])
                if not isinstance(dev_ips, list):
                    dev_ips = [dev_ips] if dev_ips else []

                for dev_ip_obj in dev_ips:
                    if isinstance(dev_ip_obj, dict):
                        dev_ip = dev_ip_obj.get("address", "").split("/")[0]
                    else:
                        dev_ip = str(dev_ip_obj).split("/")[0]

                    if dev_ip in ip_addresses:
                        if best_match and best_match != device:
                            warnings.append(
                                f"Multiple devices match IP {dev_ip}, using first match"
                            )
                        best_match = device
                        confidence = "low"
                        method = "ip_address"
                        break
                if best_match:
                    break
            if best_match:
                break

    # Validate match quality
    if best_match:
        custom_fields = best_match.get("custom_fields", {}) or {}
        provider_id = custom_fields.get("Provider_Machine_ID")

        # Check if hostname matches Provider_Machine_ID
        if provider_id and str(provider_id).lower() != hostname:
            warnings.append(
                f"Hostname '{hostname}' does not match Provider_Machine_ID '{provider_id}'"
            )

        # Check if system_id matches Provider_Machine_ID
        if provider_id and str(provider_id).lower() != system_id.lower():
            warnings.append(
                f"System ID '{system_id}' does not match Provider_Machine_ID '{provider_id}'"
            )

    return {
        "match": best_match,
        "confidence": confidence,
        "method": method,
        "warnings": warnings,
    }


def validate_link(maas_machine: dict[str, Any], netbox_device: dict[str, Any]) -> dict[str, Any]:
    """
    Validate the quality of a MAAS-NetBox link.

    Args:
        maas_machine: MAAS machine dict
        netbox_device: NetBox device/VM dict

    Returns:
        Dict with validation results:
        - valid: Whether link is valid
        - warnings: List of warnings
        - matches: Dict of what matches
    """
    warnings: list[str] = []
    matches: dict[str, Any] = {}

    hostname = maas_machine.get("hostname", "").lower()
    system_id = maas_machine.get("system_id", "").lower()

    custom_fields = netbox_device.get("custom_fields", {}) or {}
    provider_id = str(custom_fields.get("Provider_Machine_ID", "")).lower()

    # Check hostname match
    if provider_id:
        if hostname == provider_id:
            matches["hostname"] = True
        else:
            matches["hostname"] = False
            warnings.append(
                f"Hostname '{maas_machine.get('hostname')}' does not match Provider_Machine_ID '{custom_fields.get('Provider_Machine_ID')}'"
            )
    else:
        warnings.append("NetBox device has no Provider_Machine_ID")

    # Check system_id match
    if provider_id:
        if system_id == provider_id:
            matches["system_id"] = True
        else:
            matches["system_id"] = False
            if not warnings:
                warnings.append(
                    f"System ID '{maas_machine.get('system_id')}' does not match Provider_Machine_ID '{custom_fields.get('Provider_Machine_ID')}'"
                )

    # Check MAC addresses
    maas_macs = []
    maas_interfaces = maas_machine.get("interfaces", [])
    if isinstance(maas_interfaces, list):
        maas_macs = [
            iface.get("mac_address", "").lower()
            for iface in maas_interfaces
            if iface.get("mac_address")
        ]

    netbox_macs = []
    netbox_interfaces = netbox_device.get("interfaces", [])
    if isinstance(netbox_interfaces, list):
        netbox_macs = [
            iface.get("mac_address", "").lower()
            for iface in netbox_interfaces
            if iface.get("mac_address")
        ]

    if maas_macs and netbox_macs:
        common_macs = set(maas_macs) & set(netbox_macs)
        if common_macs:
            matches["mac_addresses"] = True
            matches["common_macs"] = list(common_macs)
        else:
            matches["mac_addresses"] = False
            warnings.append("No matching MAC addresses found")
    else:
        matches["mac_addresses"] = None

    # Determine overall validity
    valid = (
        matches.get("hostname") is True
        or matches.get("system_id") is True
        or matches.get("mac_addresses") is True
    )

    return {
        "valid": valid,
        "warnings": warnings,
        "matches": matches,
    }


# ---------------------------------------------------------------------------
# Network profile extraction
# ---------------------------------------------------------------------------

_BOND_PARAM_KEYS = frozenset(
    {
        "bond_mode",
        "bond_miimon",
        "bond_lacp_rate",
        "bond_xmit_hash_policy",
        "bond_num_grat_arp",
        "bond_downdelay",
        "bond_updelay",
        "bond_primary",
        "mtu",
    }
)


def extract_network_profile(machine: dict[str, Any]) -> dict[str, Any]:
    """Build a portable network profile from a MAAS machine dict.

    The returned dict is independent of MAAS-internal IDs and can be used
    to replicate the network config on a different MAAS instance.
    """
    ifaces: list[dict[str, Any]] = machine.get("interface_set") or []
    bonds: list[dict[str, Any]] = []
    physical: list[dict[str, Any]] = []
    gateway: str | None = None
    dns_servers: list[str] = []

    bond_children: dict[str, str] = {}
    for iface in ifaces:
        if iface.get("type") == "bond":
            for parent_name in iface.get("parents") or []:
                bond_children[parent_name] = iface.get("name", "")

    for iface in ifaces:
        itype = iface.get("type", "physical")

        if itype == "bond":
            params_raw = iface.get("params") or {}
            params = {k: params_raw[k] for k in _BOND_PARAM_KEYS if k in params_raw}
            links = _extract_links(iface)
            if not gateway:
                gateway = _extract_gateway(iface)
            if not dns_servers:
                dns_servers = _extract_dns(iface)
            vlan_info = _extract_vlan_info(iface)
            bonds.append(
                {
                    "name": iface.get("name") or "",
                    "parents": iface.get("parents") or [],
                    "params": params,
                    "links": links,
                    "vlan": vlan_info,
                }
            )

        elif itype == "physical":
            mtu_raw = iface.get("params") or {}
            mtu = mtu_raw.get("mtu") if isinstance(mtu_raw, dict) else None
            physical.append(
                {
                    "name": iface.get("name") or "",
                    "mac": (iface.get("mac_address") or "").lower(),
                    "mtu": mtu or iface.get("effective_mtu"),
                    "bond_parent": bond_children.get(iface.get("name", "")),
                }
            )

    return {
        "hostname": machine.get("hostname", ""),
        "bonds": bonds,
        "physical_interfaces": physical,
        "gateway": gateway,
        "dns_servers": dns_servers,
    }


def match_interfaces_by_mac(
    source_profile: dict[str, Any],
    target_iface_set: list[dict[str, Any]],
) -> dict[str, int]:
    """Map source interface names to target interface IDs using MAC addresses.

    Returns ``{source_iface_name: target_iface_id}``.
    """
    target_by_mac: dict[str, int] = {}
    for iface in target_iface_set:
        mac = (iface.get("mac_address") or "").lower()
        if mac:
            target_by_mac[mac] = iface.get("id", 0)

    mapping: dict[str, int] = {}
    for pi in source_profile.get("physical_interfaces") or []:
        mac = pi.get("mac", "").lower()
        if mac in target_by_mac:
            mapping[pi["name"]] = target_by_mac[mac]
    return mapping


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_links(iface: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in iface.get("links") or []:
        subnet = link.get("subnet") or {}
        out.append(
            {
                "mode": link.get("mode", ""),
                "ip_address": link.get("ip_address"),
                "subnet_cidr": subnet.get("cidr"),
            }
        )
    return out


def _extract_gateway(iface: dict[str, Any]) -> str | None:
    for link in iface.get("links") or []:
        subnet = link.get("subnet") or {}
        gw = subnet.get("gateway_ip")
        if gw:
            return str(gw)
    return None


def _extract_dns(iface: dict[str, Any]) -> list[str]:
    for link in iface.get("links") or []:
        subnet = link.get("subnet") or {}
        servers = subnet.get("dns_servers")
        if servers:
            return list(servers)
    return []


def _extract_vlan_info(iface: dict[str, Any]) -> dict[str, Any]:
    vlan = iface.get("vlan") or {}
    return {
        "vid": vlan.get("vid"),
        "fabric": vlan.get("fabric"),
        "fabric_id": vlan.get("fabric_id"),
        "name": vlan.get("name"),
    }
