"""Parse InfiniBand device status from ibdev2netdev and ibstat."""

from __future__ import annotations

import re
from typing import Any

# mlx5_0 port 1 ==> ibs3 (Up)
_IBDEV2NET_RE = re.compile(
    r"(?P<device>mlx5_\d+)\s+port\s+(?P<port>\d+)\s+==>\s+(?P<iface>\S+)\s+\((?P<state>\w+)\)"
)

# H100 topology: 8 IB + 2 Ethernet
H100_IB_DEVICES = frozenset(
    {"mlx5_0", "mlx5_1", "mlx5_2", "mlx5_3", "mlx5_5", "mlx5_6", "mlx5_8", "mlx5_9"}
)
H100_ETH_DEVICES = frozenset({"mlx5_4", "mlx5_7"})

# GB200 topology: 4 IB + 1 Ethernet (4-GPU nodes)
GB200_IB_DEVICES = frozenset({"mlx5_0", "mlx5_1", "mlx5_2", "mlx5_3"})
GB200_ETH_DEVICES = frozenset({"mlx5_4"})

# Backwards-compatible aliases
EXPECTED_IB_DEVICES = H100_IB_DEVICES
EXPECTED_ETH_DEVICES = H100_ETH_DEVICES

NODE_TOPOLOGIES: dict[str, dict[str, frozenset[str]]] = {
    "h100": {"ib": H100_IB_DEVICES, "eth": H100_ETH_DEVICES},
    "gb200": {"ib": GB200_IB_DEVICES, "eth": GB200_ETH_DEVICES},
}


def parse_ibdev2netdev(
    text: str,
    *,
    expected_ib_devices: set[str] | frozenset[str] | None = None,
    expected_eth_devices: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Parse ``ibdev2netdev`` output.

    Returns device list with status flags.  By default checks the H100
    topology (8 IB + 2 Ethernet).  Pass explicit device sets, or use
    :data:`NODE_TOPOLOGIES` presets for other platforms like GB200.
    """
    if expected_ib_devices is None:
        expected_ib_devices = H100_IB_DEVICES
    if expected_eth_devices is None:
        expected_eth_devices = H100_ETH_DEVICES

    if not text or not text.strip():
        return _empty_ibdev(expected_ib_devices, expected_eth_devices)

    devices: list[dict[str, str]] = []
    ports_down: list[str] = []
    ib_up: set[str] = set()
    eth_up: set[str] = set()

    for line in text.splitlines():
        m = _IBDEV2NET_RE.match(line.strip())
        if not m:
            continue
        dev = m.group("device")
        state = m.group("state")
        devices.append(
            {
                "device": dev,
                "port": m.group("port"),
                "interface": m.group("iface"),
                "state": state,
            }
        )
        if state.lower() != "up":
            ports_down.append(dev)
        elif dev in expected_ib_devices:
            ib_up.add(dev)
        elif dev in expected_eth_devices:
            eth_up.add(dev)

    missing_ib = sorted(expected_ib_devices - ib_up)
    missing_eth = sorted(expected_eth_devices - eth_up)
    all_ib_up = len(missing_ib) == 0

    severity = "ok"
    if ports_down or missing_ib:
        severity = "critical"
    elif missing_eth:
        severity = "warning"

    return {
        "devices": devices,
        "ports_down": ports_down,
        "ports_up_count": len(devices) - len(ports_down),
        "all_ib_up": all_ib_up,
        "missing_ib_devices": missing_ib,
        "missing_eth_devices": missing_eth,
        "expected_ib_count": len(expected_ib_devices),
        "expected_eth_count": len(expected_eth_devices),
        "severity": severity,
    }


# ibstat output sections
_CA_TYPE_RE = re.compile(r"CA\s+'(?P<ca>\S+)'")
_CA_FIELD_RE = re.compile(r"(?P<key>\S[\w\s]+\S)\s*:\s*(?P<val>.+)")


def parse_ibstat(text: str) -> dict[str, Any]:
    """Parse ``ibstat`` output.

    Extracts CA type, number of ports, firmware version, port state,
    physical state, rate, base LID, SM LID, link layer, and port GUID
    for each HCA.
    """
    if not text or not text.strip():
        return _empty_ibstat()

    cas: list[dict[str, Any]] = []
    current_ca: dict[str, Any] | None = None
    current_port: dict[str, Any] | None = None
    ports_down: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        ca_m = _CA_TYPE_RE.match(stripped)
        if ca_m:
            if current_ca is not None:
                if current_port is not None:
                    current_ca["ports"].append(current_port)
                    current_port = None
                cas.append(current_ca)
            current_ca = {
                "ca_name": ca_m.group("ca"),
                "ca_type": None,
                "num_ports": None,
                "firmware": None,
                "hw_version": None,
                "node_guid": None,
                "system_guid": None,
                "ports": [],
            }
            continue

        if current_ca is None:
            continue

        # Port sub-section
        port_m = re.match(r"Port\s+(\d+):", stripped)
        if port_m:
            if current_port is not None:
                current_ca["ports"].append(current_port)
            current_port = {"port_number": int(port_m.group(1))}
            continue

        kv = _CA_FIELD_RE.match(stripped)
        if not kv:
            continue
        key = kv.group("key").strip()
        val = kv.group("val").strip()

        if current_port is not None:
            _set_port_field(current_port, key, val)
            if key.lower() == "state" and val.lower() not in ("active", "active (4)"):
                ca_label = f"{current_ca['ca_name']}:{current_port['port_number']}"
                ports_down.append(ca_label)
        else:
            _set_ca_field(current_ca, key, val)

    if current_ca is not None:
        if current_port is not None:
            current_ca["ports"].append(current_port)
        cas.append(current_ca)

    total_ports = sum(len(ca["ports"]) for ca in cas)
    all_ib_up = len(ports_down) == 0

    severity = "ok"
    if ports_down:
        severity = "critical"

    return {
        "cas": cas,
        "total_ports": total_ports,
        "ports_down": ports_down,
        "ports_up_count": total_ports - len(ports_down),
        "all_ib_up": all_ib_up,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_ca_field(ca: dict[str, Any], key: str, val: str) -> None:
    kl = key.lower()
    if "ca type" in kl:
        ca["ca_type"] = val
    elif "number of ports" in kl or "num ports" in kl:
        ca["num_ports"] = int(val)
    elif "firmware" in kl:
        ca["firmware"] = val
    elif "hardware version" in kl or "hw version" in kl:
        ca["hw_version"] = val
    elif "node guid" in kl:
        ca["node_guid"] = val
    elif "system" in kl and "guid" in kl:
        ca["system_guid"] = val


def _set_port_field(port: dict[str, Any], key: str, val: str) -> None:
    kl = key.lower()
    if kl == "state":
        port["state"] = val
    elif "physical state" in kl or "phys state" in kl:
        port["physical_state"] = val
    elif kl == "rate":
        port["rate"] = val
    elif "base lid" in kl:
        port["base_lid"] = val
    elif "sm lid" in kl:
        port["sm_lid"] = val
    elif "link layer" in kl:
        port["link_layer"] = val
    elif "port guid" in kl:
        port["port_guid"] = val


def _empty_ibdev(
    expected_ib: set[str] | frozenset[str] = H100_IB_DEVICES,
    expected_eth: set[str] | frozenset[str] = H100_ETH_DEVICES,
) -> dict[str, Any]:
    return {
        "devices": [],
        "ports_down": [],
        "ports_up_count": 0,
        "all_ib_up": False,
        "missing_ib_devices": sorted(expected_ib),
        "missing_eth_devices": sorted(expected_eth),
        "expected_ib_count": len(expected_ib),
        "expected_eth_count": len(expected_eth),
        "severity": "critical",
    }


def _empty_ibstat() -> dict[str, Any]:
    return {
        "cas": [],
        "total_ports": 0,
        "ports_down": [],
        "ports_up_count": 0,
        "all_ib_up": False,
        "severity": "ok",
    }
