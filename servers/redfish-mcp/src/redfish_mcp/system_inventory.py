"""System hardware inventory: processors, memory DIMMs, and PCIe devices."""

from __future__ import annotations

import logging
from typing import Any

from .redfish import (
    RedfishClient,
    RedfishEndpoint,
    batch_get_json,
    filter_hgx_pcie_chassis,
    filter_host_chassis,
    to_abs,
)

logger = logging.getLogger("redfish_mcp.system_inventory")

_HGX_BASEBOARD_SYSTEM = "HGX_Baseboard_0"


def _build_processor_entry(cpu: dict[str, Any], url: str, source: str) -> dict[str, Any]:
    """Build a processor dict from raw Redfish data."""
    entry: dict[str, Any] = {
        "Id": cpu.get("Id"),
        "Name": cpu.get("Name"),
        "Manufacturer": cpu.get("Manufacturer"),
        "Model": cpu.get("Model"),
        "ProcessorType": cpu.get("ProcessorType"),
        "ProcessorArchitecture": cpu.get("ProcessorArchitecture"),
        "InstructionSet": cpu.get("InstructionSet"),
        "MaxSpeedMHz": cpu.get("MaxSpeedMHz"),
        "OperatingSpeedMHz": cpu.get("OperatingSpeedMHz"),
        "TotalCores": cpu.get("TotalCores"),
        "TotalEnabledCores": cpu.get("TotalEnabledCores"),
        "TotalThreads": cpu.get("TotalThreads"),
        "Socket": cpu.get("Socket"),
        "Status": cpu.get("Status"),
        "ProcessorId": cpu.get("ProcessorId"),
        "source": source,
        "url": url,
    }
    if source == "gpu_tray":
        entry["FirmwareVersion"] = cpu.get("FirmwareVersion")
        mem = cpu.get("MemorySummary")
        if isinstance(mem, dict):
            entry["MemorySummary"] = mem
    return entry


def _collect_from_system(
    c: RedfishClient,
    system_url: str,
    source: str,
    result: dict[str, Any],
) -> None:
    """Fetch Processors collection under *system_url* and append to *result*."""
    proc_url = f"{system_url}/Processors"
    proc_coll, perr = c.get_json_maybe(proc_url)
    result["sources"].append({"url": proc_url, "ok": perr is None, "error": perr})

    if perr or not proc_coll:
        result["errors"].append(f"Cannot access Processors at {proc_url}: {perr}")
        return

    cpu_urls = [
        to_abs(c.base_url, m["@odata.id"])
        for m in proc_coll.get("Members", [])
        if isinstance(m, dict) and "@odata.id" in m
    ]

    for cpu_url, cpu, cpu_err in batch_get_json(c, cpu_urls):
        result["sources"].append({"url": cpu_url, "ok": cpu_err is None, "error": cpu_err})
        if cpu_err or not cpu:
            result["errors"].append(f"Failed to get {cpu_url}: {cpu_err}")
            continue
        result["processors"].append(_build_processor_entry(cpu, cpu_url, source))


def _find_hgx_system(c: RedfishClient) -> str | None:
    """Return the @odata.id for HGX_Baseboard_0 if it exists, else None."""
    systems, err = c.get_json_maybe(f"{c.base_url}/redfish/v1/Systems")
    if err or not systems:
        return None
    for m in systems.get("Members", []):
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        if oid.rstrip("/").endswith(f"/{_HGX_BASEBOARD_SYSTEM}"):
            return oid
    return None


def collect_processor_inventory(c: RedfishClient, ep: RedfishEndpoint) -> dict[str, Any]:
    """Collect CPU/processor inventory from Systems/{id}/Processors.

    On B300 nodes with an HGX baseboard, also collects GPU and FPGA
    processors from ``HGX_Baseboard_0`` and tags each entry with a
    ``source`` field (``"host"`` or ``"gpu_tray"``).

    Works across Supermicro, Dell, Lenovo, ASRockRack, CIARA, Inspur,
    and any DMTF-compliant BMC.
    """
    result: dict[str, Any] = {
        "processors": [],
        "count": 0,
        "sources": [],
        "errors": [],
    }

    _collect_from_system(c, ep.system_url, "host", result)

    hgx_oid = _find_hgx_system(c)
    if hgx_oid:
        hgx_url = to_abs(c.base_url, hgx_oid)
        logger.info("B300 HGX baseboard detected, collecting GPU-tray processors from %s", hgx_url)
        _collect_from_system(c, hgx_url, "gpu_tray", result)

    result["count"] = len(result["processors"])
    return result


def collect_memory_inventory(c: RedfishClient, ep: RedfishEndpoint) -> dict[str, Any]:
    """Collect memory DIMM inventory from Systems/{id}/Memory.

    Returns individual DIMM details and a summary with total capacity.
    """
    result: dict[str, Any] = {
        "dimms": [],
        "count": 0,
        "total_capacity_gib": 0,
        "populated_count": 0,
        "summary": {},
        "sources": [],
        "errors": [],
    }

    mem_url = f"{ep.system_url}/Memory"
    mem_coll, merr = c.get_json_maybe(mem_url)
    result["sources"].append({"url": mem_url, "ok": merr is None, "error": merr})

    if merr or not mem_coll:
        result["errors"].append(f"Cannot access Memory: {merr}")
        return result

    total_mib = 0
    populated = 0
    speed_set: set[int] = set()
    type_set: set[str] = set()

    for member in mem_coll.get("Members", []):
        if not isinstance(member, dict) or "@odata.id" not in member:
            continue
        dimm_url = to_abs(c.base_url, member["@odata.id"])
        dimm, dimm_err = c.get_json_maybe(dimm_url)
        result["sources"].append({"url": dimm_url, "ok": dimm_err is None, "error": dimm_err})

        if dimm_err or not dimm:
            result["errors"].append(f"Failed to get {dimm_url}: {dimm_err}")
            continue

        cap_mib = dimm.get("CapacityMiB") or 0
        status = dimm.get("Status") or {}
        state = status.get("State", "")

        if state == "Absent":
            result["dimms"].append(
                {
                    "Id": dimm.get("Id"),
                    "DeviceLocator": dimm.get("DeviceLocator"),
                    "populated": False,
                }
            )
            continue

        entry = {
            "Id": dimm.get("Id"),
            "Name": dimm.get("Name"),
            "Manufacturer": dimm.get("Manufacturer"),
            "PartNumber": dimm.get("PartNumber"),
            "SerialNumber": dimm.get("SerialNumber"),
            "CapacityMiB": cap_mib,
            "OperatingSpeedMhz": dimm.get("OperatingSpeedMhz"),
            "MemoryDeviceType": dimm.get("MemoryDeviceType"),
            "BaseModuleType": dimm.get("BaseModuleType"),
            "DataWidthBits": dimm.get("DataWidthBits"),
            "BusWidthBits": dimm.get("BusWidthBits"),
            "RankCount": dimm.get("RankCount"),
            "ErrorCorrection": dimm.get("ErrorCorrection"),
            "Status": status,
            "DeviceLocator": dimm.get("DeviceLocator"),
            "MemoryLocation": dimm.get("MemoryLocation"),
            "url": dimm_url,
        }
        entry["populated"] = True
        populated += 1
        total_mib += cap_mib
        if dimm.get("OperatingSpeedMhz"):
            speed_set.add(dimm["OperatingSpeedMhz"])
        if dimm.get("MemoryDeviceType"):
            type_set.add(dimm["MemoryDeviceType"])

        result["dimms"].append(entry)

    result["count"] = len(result["dimms"])
    result["populated_count"] = populated
    result["total_capacity_gib"] = round(total_mib / 1024, 1) if total_mib else 0
    result["summary"] = {
        "total_slots": len(result["dimms"]),
        "populated_slots": populated,
        "empty_slots": len(result["dimms"]) - populated,
        "total_capacity_gib": result["total_capacity_gib"],
        "speeds_mhz": sorted(speed_set),
        "memory_types": sorted(type_set),
    }
    return result


def collect_pcie_inventory(c: RedfishClient, ep: RedfishEndpoint) -> dict[str, Any]:
    """Collect PCIe device inventory from Systems/{id}/PCIeDevices.

    Falls back to Chassis-level PCIeSlots if system-level isn't available.
    Returns GPU, NIC, NVMe, and other PCIe device details.
    """
    result: dict[str, Any] = {
        "devices": [],
        "count": 0,
        "by_type": {},
        "sources": [],
        "errors": [],
    }

    _chassis_data: list[dict[str, Any]] | None = None

    def _get_chassis() -> list[dict[str, Any]]:
        nonlocal _chassis_data
        if _chassis_data is None:
            data, err = c.get_json_maybe(f"{c.base_url}/redfish/v1/Chassis")
            _chassis_data = data.get("Members", []) if data and not err else []
        return _chassis_data

    source_tag = "system"

    pcie_url = f"{ep.system_url}/PCIeDevices"
    pcie_coll, perr = c.get_json_maybe(pcie_url)
    result["sources"].append({"url": pcie_url, "ok": perr is None, "error": perr})

    members: list[dict[str, Any]] = []

    if pcie_coll and not perr:
        members = pcie_coll.get("Members", [])
    else:
        source_tag = "host_chassis"
        for ch in filter_host_chassis(_get_chassis()):
            if not isinstance(ch, dict) or "@odata.id" not in ch:
                continue
            ch_base = to_abs(c.base_url, ch["@odata.id"])
            ch_pcie_url = ch_base + "/PCIeDevices"
            ch_pcie, ch_err = c.get_json_maybe(ch_pcie_url)
            result["sources"].append({"url": ch_pcie_url, "ok": ch_err is None, "error": ch_err})
            if ch_pcie and not ch_err:
                members.extend(ch_pcie.get("Members", []))

    if not members:
        system, _ = c.get_json_maybe(ep.system_url)
        if system:
            pcie_links = system.get("PCIeDevices") or []
            if isinstance(pcie_links, list):
                members = pcie_links
                source_tag = "system"

    # B300 GPU-tray fallback: HGX_GPU_* and HGX_ConnectX_* chassis expose
    # PCIeDevices that are invisible under the host System resource.
    if not members:
        source_tag = "hgx_chassis"
        for ch in filter_hgx_pcie_chassis(_get_chassis()):
            ch_base = to_abs(c.base_url, ch["@odata.id"])
            ch_pcie_url = f"{ch_base}/PCIeDevices"
            ch_pcie, ch_err = c.get_json_maybe(ch_pcie_url)
            result["sources"].append({"url": ch_pcie_url, "ok": ch_err is None, "error": ch_err})
            if ch_pcie and not ch_err:
                members.extend(ch_pcie.get("Members", []))

    for member in members:
        if not isinstance(member, dict) or "@odata.id" not in member:
            continue
        dev_url = to_abs(c.base_url, member["@odata.id"])
        dev, dev_err = c.get_json_maybe(dev_url)
        result["sources"].append({"url": dev_url, "ok": dev_err is None, "error": dev_err})

        if dev_err or not dev:
            result["errors"].append(f"Failed to get {dev_url}: {dev_err}")
            continue

        device_type = dev.get("DeviceType", "Unknown")
        name_lower = (dev.get("Name") or "").lower()
        desc_lower = (dev.get("Description") or "").lower()

        category = "other"
        gpu_terms = ["gpu", "graphics", "nvidia", "amd radeon"]
        if any(x in name_lower or x in desc_lower for x in gpu_terms):
            category = "gpu"
        elif any(
            x in name_lower or x in desc_lower
            for x in ["ethernet", "network", "nic", "mellanox", "connectx"]
        ):
            category = "network"
        elif any(x in name_lower or x in desc_lower for x in ["nvme", "ssd", "storage"]):
            category = "storage"
        elif any(x in name_lower or x in desc_lower for x in ["switch", "bridge", "retimer"]):
            category = "pcie_infrastructure"

        entry = {
            "Id": dev.get("Id"),
            "Name": dev.get("Name"),
            "Description": dev.get("Description"),
            "Manufacturer": dev.get("Manufacturer"),
            "Model": dev.get("Model"),
            "SerialNumber": dev.get("SerialNumber"),
            "FirmwareVersion": dev.get("FirmwareVersion"),
            "DeviceType": device_type,
            "PCIeInterface": dev.get("PCIeInterface"),
            "Status": dev.get("Status"),
            "category": category,
            "source": source_tag,
            "url": dev_url,
        }

        functions_ref = dev.get("PCIeFunctions")
        if isinstance(functions_ref, dict) and "@odata.id" in functions_ref:
            funcs_url = to_abs(c.base_url, functions_ref["@odata.id"])
            funcs, f_err = c.get_json_maybe(funcs_url)
            if funcs and not f_err:
                fn_list = []
                for fm in funcs.get("Members", []):
                    if not isinstance(fm, dict) or "@odata.id" not in fm:
                        continue
                    fn_url = to_abs(c.base_url, fm["@odata.id"])
                    fn_data, fn_err = c.get_json_maybe(fn_url)
                    if fn_data and not fn_err:
                        fn_list.append(
                            {
                                "DeviceClass": fn_data.get("DeviceClass"),
                                "DeviceId": fn_data.get("DeviceId"),
                                "VendorId": fn_data.get("VendorId"),
                                "SubsystemId": fn_data.get("SubsystemId"),
                                "SubsystemVendorId": fn_data.get("SubsystemVendorId"),
                                "FunctionType": fn_data.get("FunctionType"),
                            }
                        )
                if fn_list:
                    entry["functions"] = fn_list

        result["devices"].append(entry)

    result["count"] = len(result["devices"])
    type_counts: dict[str, int] = {}
    for d in result["devices"]:
        cat = d.get("category", "other")
        type_counts[cat] = type_counts.get(cat, 0) + 1
    result["by_type"] = type_counts
    return result
