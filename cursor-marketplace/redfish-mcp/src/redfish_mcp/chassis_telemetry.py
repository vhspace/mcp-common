"""Chassis telemetry collection: power supplies, thermal sensors, and fans."""

from __future__ import annotations

from typing import Any

from .redfish import RedfishClient, filter_host_chassis, to_abs


def discover_chassis(c: RedfishClient) -> list[str]:
    """Return @odata.id paths for telemetry-relevant Chassis members.

    Includes host chassis (via *filter_host_chassis*) **plus** any
    ``HGX_GPU_Baseboard`` members, which carry aggregate power / thermal
    telemetry for the GPU tray on B300-class systems via the Redfish 2024+
    PowerSubsystem / ThermalSubsystem endpoints.

    Individual HGX GPUs, NVSwitches, ERoT, and IRoT members are still
    filtered out to avoid 80+ redundant HTTP round-trips.
    """
    root, err = c.get_json_maybe(f"{c.base_url}/redfish/v1/Chassis")
    if err or not root:
        return []
    members = root.get("Members", [])
    host = filter_host_chassis(members)

    gpu_baseboard = [
        m
        for m in members
        if isinstance(m, dict)
        and isinstance(m.get("@odata.id"), str)
        and m["@odata.id"].rstrip("/").rsplit("/", 1)[-1].startswith("HGX_GPU_Baseboard")
    ]
    # Cap baseboard members to avoid runaway iteration on exotic hardware,
    # mirroring the MAX_HOST_CHASSIS guard applied by filter_host_chassis().
    gpu_baseboard = gpu_baseboard[:2]

    seen: set[str] = set()
    paths: list[str] = []
    for m in host + gpu_baseboard:
        oid = m.get("@odata.id")
        if isinstance(oid, str) and oid not in seen:
            seen.add(oid)
            paths.append(oid)
    return paths


def collect_power_info(c: RedfishClient) -> dict[str, Any]:
    """Collect power supply status and consumption from Chassis/Power.

    Works across Supermicro, Dell, Lenovo, ASRockRack, CIARA, and any
    DMTF-compliant BMC.  Tries legacy /Power first, then the newer
    PowerSubsystem/PowerSupplies path.
    """
    result: dict[str, Any] = {
        "power_supplies": [],
        "power_control": [],
        "voltages": [],
        "sources": [],
        "errors": [],
    }

    chassis_paths = discover_chassis(c)
    if not chassis_paths:
        result["errors"].append("No Chassis members found")
        result["psu_count"] = 0
        return result

    for ch_path in chassis_paths:
        ch_url = to_abs(c.base_url, ch_path)

        # Legacy Redfish Power resource
        power_url = f"{ch_url}/Power"
        power, perr = c.get_json_maybe(power_url)
        result["sources"].append({"url": power_url, "ok": perr is None, "error": perr})

        if power and not perr:
            for ps in power.get("PowerSupplies", []):
                result["power_supplies"].append(
                    {
                        "Name": ps.get("Name"),
                        "Model": ps.get("Model"),
                        "SerialNumber": ps.get("SerialNumber"),
                        "Manufacturer": ps.get("Manufacturer"),
                        "PowerCapacityWatts": ps.get("PowerCapacityWatts"),
                        "LastPowerOutputWatts": ps.get("LastPowerOutputWatts"),
                        "PowerInputWatts": ps.get("PowerInputWatts"),
                        "PowerOutputWatts": ps.get("PowerOutputWatts"),
                        "LineInputVoltage": ps.get("LineInputVoltage"),
                        "LineInputVoltageType": ps.get("LineInputVoltageType"),
                        "Status": ps.get("Status"),
                        "EfficiencyRating": ps.get("EfficiencyPercent"),
                        "FirmwareVersion": ps.get("FirmwareVersion"),
                        "SparePartNumber": ps.get("SparePartNumber"),
                        "source": power_url,
                    }
                )

            for pc in power.get("PowerControl", []):
                result["power_control"].append(
                    {
                        "Name": pc.get("Name"),
                        "PowerConsumedWatts": pc.get("PowerConsumedWatts"),
                        "PowerRequestedWatts": pc.get("PowerRequestedWatts"),
                        "PowerAvailableWatts": pc.get("PowerAvailableWatts"),
                        "PowerCapacityWatts": pc.get("PowerCapacityWatts"),
                        "PowerAllocatedWatts": pc.get("PowerAllocatedWatts"),
                        "PowerMetrics": pc.get("PowerMetrics"),
                        "PowerLimit": pc.get("PowerLimit"),
                        "Status": pc.get("Status"),
                        "source": power_url,
                    }
                )

            for v in power.get("Voltages", []):
                result["voltages"].append(
                    {
                        "Name": v.get("Name"),
                        "ReadingVolts": v.get("ReadingVolts"),
                        "Status": v.get("Status"),
                        "UpperThresholdCritical": v.get("UpperThresholdCritical"),
                        "LowerThresholdCritical": v.get("LowerThresholdCritical"),
                        "source": power_url,
                    }
                )
            continue

        # Newer Redfish PowerSubsystem path (2024+)
        subsys_url = f"{ch_url}/PowerSubsystem"
        subsys, subsys_err = c.get_json_maybe(subsys_url)
        result["sources"].append({"url": subsys_url, "ok": subsys_err is None, "error": subsys_err})

        if not subsys or subsys_err:
            continue

        # Aggregate power metrics from the subsystem resource itself
        allocation = subsys.get("Allocation") or {}
        if isinstance(allocation, dict) and allocation:
            # RequestedWatts is the best available approximation for actual
            # consumption from the Allocation object; PowerSubsystem does not
            # expose metered consumption at this level.
            result["power_control"].append(
                {
                    "Name": subsys.get("Name", "PowerSubsystem"),
                    "PowerConsumedWatts": allocation.get("RequestedWatts"),
                    "PowerRequestedWatts": allocation.get("RequestedWatts"),
                    "PowerAvailableWatts": allocation.get("AllocatedWatts"),
                    "PowerCapacityWatts": subsys.get("CapacityWatts"),
                    "Status": subsys.get("Status"),
                    "schema": "subsystem",
                    "source": subsys_url,
                }
            )

        # Individual PSU data from PowerSupplies collection
        supplies_ref = subsys.get("PowerSupplies", {})
        if isinstance(supplies_ref, dict) and "@odata.id" in supplies_ref:
            ps_url = to_abs(c.base_url, supplies_ref["@odata.id"])
        else:
            ps_url = f"{subsys_url}/PowerSupplies"
        ps_coll, ps_err = c.get_json_maybe(ps_url)
        result["sources"].append({"url": ps_url, "ok": ps_err is None, "error": ps_err})

        if ps_coll and not ps_err:
            for m in ps_coll.get("Members", []):
                if not isinstance(m, dict) or "@odata.id" not in m:
                    continue
                psu_url = to_abs(c.base_url, m["@odata.id"])
                psu, psu_err = c.get_json_maybe(psu_url)
                if psu_err or not psu:
                    result["errors"].append(f"Failed to get {psu_url}: {psu_err}")
                    continue
                result["power_supplies"].append(
                    {
                        "Name": psu.get("Name"),
                        "Model": psu.get("Model"),
                        "SerialNumber": psu.get("SerialNumber"),
                        "Manufacturer": psu.get("Manufacturer"),
                        "PowerCapacityWatts": psu.get("PowerCapacityWatts"),
                        "PowerOutputWatts": psu.get("OutputWatts"),
                        "PowerInputWatts": psu.get("InputWatts"),
                        "Status": psu.get("Status"),
                        "FirmwareVersion": psu.get("FirmwareVersion"),
                        "EfficiencyRating": psu.get("EfficiencyRatings"),
                        "source": psu_url,
                    }
                )

    result["psu_count"] = len(result["power_supplies"])
    return result


def collect_thermal_info(c: RedfishClient) -> dict[str, Any]:
    """Collect temperature readings and fan speeds from Chassis/Thermal.

    Tries legacy /Thermal first, then the newer ThermalSubsystem path.
    """
    result: dict[str, Any] = {
        "temperatures": [],
        "fans": [],
        "sources": [],
        "errors": [],
    }

    chassis_paths = discover_chassis(c)
    if not chassis_paths:
        result["errors"].append("No Chassis members found")
        return result

    for ch_path in chassis_paths:
        ch_url = to_abs(c.base_url, ch_path)

        # Legacy Thermal resource
        thermal_url = f"{ch_url}/Thermal"
        thermal, terr = c.get_json_maybe(thermal_url)
        result["sources"].append({"url": thermal_url, "ok": terr is None, "error": terr})

        if thermal and not terr:
            for t in thermal.get("Temperatures", []):
                result["temperatures"].append(
                    {
                        "Name": t.get("Name"),
                        "ReadingCelsius": t.get("ReadingCelsius"),
                        "Status": t.get("Status"),
                        "UpperThresholdCritical": t.get("UpperThresholdCritical"),
                        "UpperThresholdFatal": t.get("UpperThresholdFatal"),
                        "LowerThresholdCritical": t.get("LowerThresholdCritical"),
                        "PhysicalContext": t.get("PhysicalContext"),
                        "source": thermal_url,
                    }
                )

            for f in thermal.get("Fans", []):
                result["fans"].append(
                    {
                        "Name": f.get("Name") or f.get("FanName"),
                        "Reading": f.get("Reading"),
                        "ReadingUnits": f.get("ReadingUnits"),
                        "Status": f.get("Status"),
                        "UpperThresholdCritical": f.get("UpperThresholdCritical"),
                        "LowerThresholdCritical": f.get("LowerThresholdCritical"),
                        "PhysicalContext": f.get("PhysicalContext"),
                        "source": thermal_url,
                    }
                )
            continue

        # Newer ThermalSubsystem path
        ts_url = f"{ch_url}/ThermalSubsystem"
        ts, ts_err = c.get_json_maybe(ts_url)
        result["sources"].append({"url": ts_url, "ok": ts_err is None, "error": ts_err})

        if ts and not ts_err:
            # ThermalMetrics
            metrics_ref = ts.get("ThermalMetrics", {})
            if isinstance(metrics_ref, dict) and "@odata.id" in metrics_ref:
                metrics_url = to_abs(c.base_url, metrics_ref["@odata.id"])
                metrics, m_err = c.get_json_maybe(metrics_url)
                result["sources"].append({"url": metrics_url, "ok": m_err is None, "error": m_err})
                if metrics and not m_err:
                    for t in metrics.get("TemperatureReadingsCelsius", []):
                        result["temperatures"].append(
                            {
                                "Name": (t.get("DataSourceUri", "").rsplit("/", 1)[-1]),
                                "ReadingCelsius": t.get("Reading"),
                                "schema": "subsystem",
                                "source": metrics_url,
                            }
                        )
                elif m_err:
                    result["errors"].append(f"ThermalMetrics fetch failed: {m_err}")

            # Fans collection
            fans_ref = ts.get("Fans", {})
            if isinstance(fans_ref, dict) and "@odata.id" in fans_ref:
                fans_url = to_abs(c.base_url, fans_ref["@odata.id"])
                fans_coll, f_err = c.get_json_maybe(fans_url)
                result["sources"].append({"url": fans_url, "ok": f_err is None, "error": f_err})
                if fans_coll and not f_err:
                    for m in fans_coll.get("Members", []):
                        if not isinstance(m, dict) or "@odata.id" not in m:
                            continue
                        fan_url = to_abs(c.base_url, m["@odata.id"])
                        fan, fan_err = c.get_json_maybe(fan_url)
                        if fan and not fan_err:
                            speed = fan.get("SpeedPercent", {})
                            result["fans"].append(
                                {
                                    "Name": fan.get("Name"),
                                    "SpeedPercent": (
                                        speed.get("Reading") if isinstance(speed, dict) else speed
                                    ),
                                    "Status": fan.get("Status"),
                                    "schema": "subsystem",
                                    "source": fan_url,
                                }
                            )
                        elif fan_err:
                            result["errors"].append(f"Fan fetch failed {fan_url}: {fan_err}")
                elif f_err:
                    result["errors"].append(f"Fans collection fetch failed: {f_err}")

    result["temperature_count"] = len(result["temperatures"])
    result["fan_count"] = len(result["fans"])
    return result
