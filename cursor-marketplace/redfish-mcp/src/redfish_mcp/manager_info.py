"""BMC/Manager information and network configuration."""

from __future__ import annotations

from typing import Any

from .redfish import RedfishClient, _pick_host_manager, to_abs


def _discover_manager(c: RedfishClient) -> tuple[str | None, list[dict[str, Any]]]:
    """Discover the host-server Manager member.

    On multi-manager systems (e.g. Dell B300 with iDRAC + HGX BMC),
    prefers ``iDRAC.Embedded.1`` over ``HGX_*`` managers.

    Returns (manager_url, sources).
    """
    sources: list[dict[str, Any]] = []
    mgr_root_url = f"{c.base_url}/redfish/v1/Managers"
    mgr_root, err = c.get_json_maybe(mgr_root_url)
    sources.append({"url": mgr_root_url, "ok": err is None, "error": err})

    if err or not mgr_root:
        return None, sources

    members = mgr_root.get("Members", [])
    if not members:
        return None, sources

    try:
        chosen = _pick_host_manager(members)
    except RuntimeError:
        return None, sources

    return to_abs(c.base_url, chosen["@odata.id"]), sources


def collect_manager_info(c: RedfishClient) -> dict[str, Any]:
    """Collect BMC/Manager details: firmware, model, UUID, status, datetime.

    Works across all vendors (Supermicro, Dell iDRAC, Lenovo XCC,
    ASRockRack, CIARA, Inspur, HPE iLO).
    """
    result: dict[str, Any] = {
        "manager": None,
        "sources": [],
        "errors": [],
    }

    mgr_url, sources = _discover_manager(c)
    result["sources"].extend(sources)

    if not mgr_url:
        result["errors"].append("No Manager members found")
        return result

    mgr, merr = c.get_json_maybe(mgr_url)
    result["sources"].append({"url": mgr_url, "ok": merr is None, "error": merr})

    if merr or not mgr:
        result["errors"].append(f"Failed to get Manager: {merr}")
        return result

    result["manager"] = {
        "Id": mgr.get("Id"),
        "Name": mgr.get("Name"),
        "ManagerType": mgr.get("ManagerType"),
        "FirmwareVersion": mgr.get("FirmwareVersion"),
        "Model": mgr.get("Model"),
        "UUID": mgr.get("UUID"),
        "DateTime": mgr.get("DateTime"),
        "DateTimeLocalOffset": mgr.get("DateTimeLocalOffset"),
        "PowerState": mgr.get("PowerState"),
        "Status": mgr.get("Status"),
        "url": mgr_url,
    }

    # Network protocol (SSH, HTTPS, IPMI, SNMP port/enabled)
    net_proto_ref = mgr.get("NetworkProtocol")
    if isinstance(net_proto_ref, dict) and "@odata.id" in net_proto_ref:
        np_url = to_abs(c.base_url, net_proto_ref["@odata.id"])
        np, np_err = c.get_json_maybe(np_url)
        result["sources"].append({"url": np_url, "ok": np_err is None, "error": np_err})

        if np and not np_err:
            protocols: dict[str, Any] = {}
            for proto_name in [
                "HTTP",
                "HTTPS",
                "SSH",
                "IPMI",
                "SNMP",
                "VirtualMedia",
                "KVMIP",
                "SSDP",
                "Telnet",
                "NTP",
                "DHCP",
                "DHCPv6",
            ]:
                proto_data = np.get(proto_name)
                if isinstance(proto_data, dict):
                    protocols[proto_name] = {
                        "ProtocolEnabled": proto_data.get("ProtocolEnabled"),
                        "Port": proto_data.get("Port"),
                    }
                elif proto_data is not None:
                    protocols[proto_name] = proto_data
            result["manager"]["network_protocols"] = protocols
            result["manager"]["hostname_bmc"] = np.get("HostName")
            result["manager"]["fqdn_bmc"] = np.get("FQDN")

    return result


def collect_manager_ethernet(c: RedfishClient) -> dict[str, Any]:
    """Collect BMC network interface configuration.

    Returns IP addresses, MAC, DHCP config, VLAN for the BMC
    management interfaces.
    """
    result: dict[str, Any] = {
        "interfaces": [],
        "count": 0,
        "sources": [],
        "errors": [],
    }

    mgr_url, sources = _discover_manager(c)
    result["sources"].extend(sources)

    if not mgr_url:
        result["errors"].append("No Manager members found")
        return result

    eth_url = f"{mgr_url}/EthernetInterfaces"
    eth_coll, eth_err = c.get_json_maybe(eth_url)
    result["sources"].append({"url": eth_url, "ok": eth_err is None, "error": eth_err})

    if eth_err or not eth_coll:
        result["errors"].append(f"Cannot access Manager EthernetInterfaces: {eth_err}")
        return result

    for member in eth_coll.get("Members", []):
        if not isinstance(member, dict) or "@odata.id" not in member:
            continue
        iface_url = to_abs(c.base_url, member["@odata.id"])
        iface, iface_err = c.get_json_maybe(iface_url)
        result["sources"].append({"url": iface_url, "ok": iface_err is None, "error": iface_err})

        if iface_err or not iface:
            result["errors"].append(f"Failed to get {iface_url}: {iface_err}")
            continue

        ipv4_addrs = iface.get("IPv4Addresses", [])
        ipv6_addrs = iface.get("IPv6Addresses", [])

        entry = {
            "Id": iface.get("Id"),
            "Name": iface.get("Name"),
            "Description": iface.get("Description"),
            "MACAddress": iface.get("MACAddress"),
            "PermanentMACAddress": iface.get("PermanentMACAddress"),
            "SpeedMbps": iface.get("SpeedMbps"),
            "AutoNeg": iface.get("AutoNeg"),
            "FullDuplex": iface.get("FullDuplex"),
            "LinkStatus": iface.get("LinkStatus"),
            "InterfaceEnabled": iface.get("InterfaceEnabled"),
            "Status": iface.get("Status"),
            "HostName": iface.get("HostName"),
            "FQDN": iface.get("FQDN"),
            "IPv4Addresses": [
                {
                    "Address": a.get("Address"),
                    "SubnetMask": a.get("SubnetMask"),
                    "Gateway": a.get("Gateway"),
                    "AddressOrigin": a.get("AddressOrigin"),
                }
                for a in ipv4_addrs
                if isinstance(a, dict)
            ],
            "IPv6Addresses": [
                {
                    "Address": a.get("Address"),
                    "PrefixLength": a.get("PrefixLength"),
                    "AddressOrigin": a.get("AddressOrigin"),
                    "AddressState": a.get("AddressState"),
                }
                for a in ipv6_addrs
                if isinstance(a, dict)
            ],
            "VLAN": iface.get("VLAN"),
            "DHCPv4": iface.get("DHCPv4"),
            "DHCPv6": iface.get("DHCPv6"),
            "NameServers": iface.get("NameServers"),
            "StaticNameServers": iface.get("StaticNameServers"),
            "url": iface_url,
        }

        result["interfaces"].append(entry)

    result["count"] = len(result["interfaces"])
    return result
