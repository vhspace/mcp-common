"""Tests for chassis_telemetry, system_inventory, and manager_info modules."""

from __future__ import annotations

import unittest
from typing import ClassVar
from unittest.mock import MagicMock

from redfish_mcp.chassis_telemetry import (
    collect_power_info,
    collect_thermal_info,
    discover_chassis,
)
from redfish_mcp.manager_info import collect_manager_ethernet, collect_manager_info
from redfish_mcp.redfish import RedfishEndpoint
from redfish_mcp.system_inventory import (
    collect_memory_inventory,
    collect_pcie_inventory,
    collect_processor_inventory,
)


def _mock_client(responses: dict[str, tuple]) -> MagicMock:
    """Create a mock RedfishClient that returns canned responses by URL suffix."""
    c = MagicMock()
    c.base_url = "https://10.0.0.1"

    def get_json_maybe(url: str):
        # Match longest suffix first so /Managers/1 wins over /Managers
        for suffix, (data, err) in sorted(responses.items(), key=lambda x: -len(x[0])):
            if url.endswith(suffix):
                return data, err
        return None, f"404 not found: {url}"

    c.get_json_maybe = MagicMock(side_effect=get_json_maybe)
    return c


class TestCollectPowerInfo(unittest.TestCase):
    def test_legacy_power(self):
        responses = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                None,
            ),
            "/redfish/v1/Chassis/1/Power": (
                {
                    "PowerSupplies": [
                        {
                            "Name": "PSU1",
                            "PowerCapacityWatts": 2000,
                            "LastPowerOutputWatts": 850,
                            "Status": {"State": "Enabled", "Health": "OK"},
                        }
                    ],
                    "PowerControl": [{"Name": "System", "PowerConsumedWatts": 1650}],
                    "Voltages": [{"Name": "12V", "ReadingVolts": 12.1, "Status": {"Health": "OK"}}],
                },
                None,
            ),
        }
        c = _mock_client(responses)
        result = collect_power_info(c)
        assert result["psu_count"] == 1
        assert result["power_supplies"][0]["Name"] == "PSU1"
        assert result["power_control"][0]["PowerConsumedWatts"] == 1650
        assert result["voltages"][0]["ReadingVolts"] == 12.1
        assert not result["errors"]

    def test_no_chassis(self):
        c = _mock_client({"/redfish/v1/Chassis": (None, "connection refused")})
        result = collect_power_info(c)
        assert result["psu_count"] == 0
        assert "No Chassis members found" in result["errors"]


class TestCollectThermalInfo(unittest.TestCase):
    def test_legacy_thermal(self):
        responses = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                None,
            ),
            "/redfish/v1/Chassis/1/Thermal": (
                {
                    "Temperatures": [
                        {
                            "Name": "CPU1 Temp",
                            "ReadingCelsius": 62,
                            "Status": {"Health": "OK"},
                            "PhysicalContext": "CPU",
                        }
                    ],
                    "Fans": [
                        {
                            "Name": "Fan1",
                            "Reading": 8500,
                            "ReadingUnits": "RPM",
                            "Status": {"Health": "OK"},
                        }
                    ],
                },
                None,
            ),
        }
        c = _mock_client(responses)
        result = collect_thermal_info(c)
        assert result["temperature_count"] == 1
        assert result["temperatures"][0]["ReadingCelsius"] == 62
        assert result["fan_count"] == 1
        assert result["fans"][0]["Reading"] == 8500


class TestDiscoverChassis(unittest.TestCase):
    """discover_chassis() includes host chassis AND HGX_GPU_Baseboard members."""

    B300_MEMBERS: ClassVar[list[dict[str, str]]] = [
        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
        {"@odata.id": "/redfish/v1/Chassis/Enclosure.Internal.0-0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_1"},
        {"@odata.id": "/redfish/v1/Chassis/HGX_NVSwitch_0"},
        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_0"},
        {"@odata.id": "/redfish/v1/Chassis/IRoT_NVSwitch_0"},
    ]

    def test_b300_includes_baseboard(self):
        c = _mock_client({"/redfish/v1/Chassis": ({"Members": self.B300_MEMBERS}, None)})
        paths = discover_chassis(c)
        assert "/redfish/v1/Chassis/System.Embedded.1" in paths
        assert "/redfish/v1/Chassis/Enclosure.Internal.0-0" in paths
        assert "/redfish/v1/Chassis/HGX_GPU_Baseboard_0" in paths

    def test_b300_excludes_individual_hgx(self):
        c = _mock_client({"/redfish/v1/Chassis": ({"Members": self.B300_MEMBERS}, None)})
        paths = discover_chassis(c)
        assert "/redfish/v1/Chassis/HGX_GPU_0" not in paths
        assert "/redfish/v1/Chassis/HGX_NVSwitch_0" not in paths
        assert "/redfish/v1/Chassis/ERoT_GPU_0" not in paths
        assert "/redfish/v1/Chassis/IRoT_NVSwitch_0" not in paths

    def test_single_chassis_no_hgx(self):
        c = _mock_client(
            {
                "/redfish/v1/Chassis": (
                    {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                    None,
                )
            }
        )
        paths = discover_chassis(c)
        assert paths == ["/redfish/v1/Chassis/1"]

    def test_no_chassis(self):
        c = _mock_client({"/redfish/v1/Chassis": (None, "connection refused")})
        assert discover_chassis(c) == []


class TestPowerSubsystemFallback(unittest.TestCase):
    """Legacy /Power 404 falls back to /PowerSubsystem."""

    def test_subsystem_fallback_psu(self):
        """When /Power returns 404, PSU data comes from PowerSubsystem."""
        resp = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"}]},
                None,
            ),
            "/Chassis/HGX_GPU_Baseboard_0/Power": (None, "404 not found"),
            "/Chassis/HGX_GPU_Baseboard_0/PowerSubsystem": (
                {
                    "Name": "GPU Tray Power",
                    "CapacityWatts": 10000,
                    "Allocation": {"RequestedWatts": 5200, "AllocatedWatts": 8000},
                    "Status": {"State": "Enabled", "Health": "OK"},
                    "PowerSupplies": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/PowerSubsystem/PowerSupplies"
                    },
                },
                None,
            ),
            "/PowerSubsystem/PowerSupplies": (
                {
                    "Members": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/PowerSubsystem/PowerSupplies/0"
                        }
                    ]
                },
                None,
            ),
            "/PowerSupplies/0": (
                {
                    "Name": "GPU_PSU_0",
                    "Model": "HGX-PSU-2400",
                    "OutputWatts": 1200,
                    "InputWatts": 1300,
                    "PowerCapacityWatts": 2400,
                    "Status": {"State": "Enabled", "Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(resp)
        result = collect_power_info(c)
        assert result["psu_count"] == 1
        assert result["power_supplies"][0]["Name"] == "GPU_PSU_0"
        assert result["power_supplies"][0]["PowerOutputWatts"] == 1200
        assert result["power_supplies"][0]["PowerInputWatts"] == 1300
        assert len(result["power_control"]) == 1
        assert result["power_control"][0]["PowerConsumedWatts"] == 5200
        assert result["power_control"][0]["PowerCapacityWatts"] == 10000
        assert not result["errors"]

    def test_both_power_paths_404(self):
        """When both /Power and /PowerSubsystem return 404, skip gracefully."""
        resp = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                None,
            ),
            "/Chassis/1/Power": (None, "404 not found"),
            "/Chassis/1/PowerSubsystem": (None, "404 not found"),
        }
        c = _mock_client(resp)
        result = collect_power_info(c)
        assert result["psu_count"] == 0
        assert result["power_supplies"] == []
        assert result["power_control"] == []
        assert not result["errors"]

    def test_subsystem_no_allocation(self):
        """PowerSubsystem without Allocation still collects PSU data."""
        resp = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                None,
            ),
            "/Chassis/1/Power": (None, "404 not found"),
            "/Chassis/1/PowerSubsystem": (
                {
                    "Name": "PowerSubsystem",
                    "PowerSupplies": {
                        "@odata.id": "/redfish/v1/Chassis/1/PowerSubsystem/PowerSupplies"
                    },
                },
                None,
            ),
            "/PowerSubsystem/PowerSupplies": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/1/PowerSubsystem/PowerSupplies/0"}
                    ]
                },
                None,
            ),
            "/PowerSupplies/0": (
                {"Name": "PSU_0", "OutputWatts": 500, "Status": {"Health": "OK"}},
                None,
            ),
        }
        c = _mock_client(resp)
        result = collect_power_info(c)
        assert result["psu_count"] == 1
        assert result["power_control"] == []


class TestThermalSubsystemFallback(unittest.TestCase):
    """Legacy /Thermal 404 falls back to /ThermalSubsystem."""

    def test_subsystem_fallback_temps_and_fans(self):
        resp = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"}]},
                None,
            ),
            "/Chassis/HGX_GPU_Baseboard_0/Thermal": (None, "404 not found"),
            "/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem": (
                {
                    "ThermalMetrics": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem/ThermalMetrics"
                    },
                    "Fans": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem/Fans"
                    },
                },
                None,
            ),
            "/ThermalMetrics": (
                {
                    "TemperatureReadingsCelsius": [
                        {
                            "Reading": 72,
                            "DataSourceUri": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/Sensors/GPU0_Temp",
                        },
                        {
                            "Reading": 68,
                            "DataSourceUri": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/Sensors/GPU1_Temp",
                        },
                    ]
                },
                None,
            ),
            "/ThermalSubsystem/Fans": (
                {
                    "Members": [
                        {
                            "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem/Fans/Fan_0"
                        }
                    ]
                },
                None,
            ),
            "/Fans/Fan_0": (
                {
                    "Name": "GPU_Fan_0",
                    "SpeedPercent": {"Reading": 55},
                    "Status": {"State": "Enabled", "Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(resp)
        result = collect_thermal_info(c)
        assert result["temperature_count"] == 2
        assert result["temperatures"][0]["ReadingCelsius"] == 72
        assert result["temperatures"][0]["Name"] == "GPU0_Temp"
        assert result["fan_count"] == 1
        assert result["fans"][0]["Name"] == "GPU_Fan_0"
        assert result["fans"][0]["SpeedPercent"] == 55
        assert not result["errors"]

    def test_both_thermal_paths_404(self):
        resp = {
            "/redfish/v1/Chassis": (
                {"Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]},
                None,
            ),
            "/Chassis/1/Thermal": (None, "404 not found"),
            "/Chassis/1/ThermalSubsystem": (None, "404 not found"),
        }
        c = _mock_client(resp)
        result = collect_thermal_info(c)
        assert result["temperature_count"] == 0
        assert result["fan_count"] == 0
        assert result["temperatures"] == []
        assert result["fans"] == []
        assert not result["errors"]


class TestB300MixedChassis(unittest.TestCase):
    """B300: host chassis uses legacy, HGX baseboard uses subsystem."""

    def _b300_responses(self):
        return {
            "/redfish/v1/Chassis": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0"},
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
                        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_0"},
                    ]
                },
                None,
            ),
            # Dell host: legacy works
            "/Chassis/System.Embedded.1/Power": (
                {
                    "PowerSupplies": [
                        {"Name": "PSU1", "PowerCapacityWatts": 2400, "Status": {"Health": "OK"}}
                    ],
                    "PowerControl": [{"Name": "Server Power", "PowerConsumedWatts": 1650}],
                    "Voltages": [],
                },
                None,
            ),
            "/Chassis/System.Embedded.1/Thermal": (
                {
                    "Temperatures": [
                        {"Name": "Inlet Temp", "ReadingCelsius": 24, "PhysicalContext": "Intake"}
                    ],
                    "Fans": [{"Name": "Fan1", "Reading": 7200, "ReadingUnits": "RPM"}],
                },
                None,
            ),
            # HGX baseboard: legacy 404, subsystem works
            "/Chassis/HGX_GPU_Baseboard_0/Power": (None, "404 not found"),
            "/Chassis/HGX_GPU_Baseboard_0/PowerSubsystem": (
                {
                    "Name": "GPU Tray Power",
                    "CapacityWatts": 10200,
                    "Allocation": {"RequestedWatts": 5400, "AllocatedWatts": 10200},
                    "PowerSupplies": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/PowerSubsystem/PowerSupplies"
                    },
                },
                None,
            ),
            "/PowerSubsystem/PowerSupplies": ({"Members": []}, None),
            "/Chassis/HGX_GPU_Baseboard_0/Thermal": (None, "404 not found"),
            "/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem": (
                {
                    "ThermalMetrics": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem/ThermalMetrics"
                    },
                    "Fans": {
                        "@odata.id": "/redfish/v1/Chassis/HGX_GPU_Baseboard_0/ThermalSubsystem/Fans"
                    },
                },
                None,
            ),
            "/ThermalMetrics": (
                {
                    "TemperatureReadingsCelsius": [
                        {"Reading": 71, "DataSourceUri": "/Sensors/GPU0_Temp"},
                    ]
                },
                None,
            ),
            "/ThermalSubsystem/Fans": ({"Members": []}, None),
        }

    def test_power_mixed_schemas(self):
        c = _mock_client(self._b300_responses())
        result = collect_power_info(c)
        assert result["psu_count"] == 1
        assert result["power_supplies"][0]["Name"] == "PSU1"
        pc_names = [pc["Name"] for pc in result["power_control"]]
        assert "Server Power" in pc_names
        assert "GPU Tray Power" in pc_names
        assert not result["errors"]

    def test_thermal_mixed_schemas(self):
        c = _mock_client(self._b300_responses())
        result = collect_thermal_info(c)
        assert result["temperature_count"] == 2
        names = [t["Name"] for t in result["temperatures"]]
        assert "Inlet Temp" in names
        assert "GPU0_Temp" in names
        assert result["fan_count"] == 1
        assert result["fans"][0]["Name"] == "Fan1"
        assert not result["errors"]


class TestCollectProcessorInventory(unittest.TestCase):
    def test_two_cpus(self):
        responses = {
            "/Processors": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/1/Processors/CPU.Socket.1"},
                        {"@odata.id": "/redfish/v1/Systems/1/Processors/CPU.Socket.2"},
                    ]
                },
                None,
            ),
            "CPU.Socket.1": (
                {
                    "Id": "CPU.Socket.1",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "TotalThreads": 192,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "CPU.Socket.2": (
                {
                    "Id": "CPU.Socket.2",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "TotalThreads": 192,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 2
        assert result["processors"][0]["TotalCores"] == 96

    def test_no_processors_endpoint(self):
        c = _mock_client({"/Processors": (None, "404")})
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 0
        assert result["errors"]

    def test_host_processors_have_source_tag(self):
        responses = {
            "/Systems": (
                {"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]},
                None,
            ),
            "/Processors": (
                {
                    "Members": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.1"
                        }
                    ]
                },
                None,
            ),
            "/CPU.Socket.1": (
                {
                    "Id": "CPU.Socket.1",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/System.Embedded.1"
        )
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 1
        assert result["processors"][0]["source"] == "host"


class TestB300GpuProcessorDiscovery(unittest.TestCase):
    """GPU-tray processor discovery on B300 nodes (issue #74)."""

    def test_b300_discovers_gpu_processors(self):
        """B300 with HGX_Baseboard_0 returns both host CPUs and GPU processors."""
        responses = {
            "/Systems": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
                        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
                    ]
                },
                None,
            ),
            "/System.Embedded.1/Processors": (
                {
                    "Members": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.1"
                        },
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.2"
                        },
                    ]
                },
                None,
            ),
            "/CPU.Socket.1": (
                {
                    "Id": "CPU.Socket.1",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/CPU.Socket.2": (
                {
                    "Id": "CPU.Socket.2",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/HGX_Baseboard_0/Processors": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0"},
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_1"},
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Processors/FPGA_0"},
                    ]
                },
                None,
            ),
            "/GPU_0": (
                {
                    "Id": "GPU_0",
                    "Name": "NVIDIA B200 GPU 0",
                    "Manufacturer": "NVIDIA",
                    "Model": "B200",
                    "ProcessorType": "GPU",
                    "MaxSpeedMHz": 2100,
                    "TotalCores": 1,
                    "FirmwareVersion": "97.00.7A.00.01",
                    "MemorySummary": {"TotalSystemMemoryGiB": 192, "MemoryType": "HBM3e"},
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/GPU_1": (
                {
                    "Id": "GPU_1",
                    "Name": "NVIDIA B200 GPU 1",
                    "Manufacturer": "NVIDIA",
                    "Model": "B200",
                    "ProcessorType": "GPU",
                    "MaxSpeedMHz": 2100,
                    "TotalCores": 1,
                    "FirmwareVersion": "97.00.7A.00.01",
                    "MemorySummary": {"TotalSystemMemoryGiB": 192, "MemoryType": "HBM3e"},
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/FPGA_0": (
                {
                    "Id": "FPGA_0",
                    "Name": "FPGA_0",
                    "Manufacturer": "NVIDIA",
                    "ProcessorType": "FPGA",
                    "FirmwareVersion": "2.8.0",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/System.Embedded.1"
        )
        result = collect_processor_inventory(c, ep)

        assert result["count"] == 5
        host_procs = [p for p in result["processors"] if p["source"] == "host"]
        gpu_procs = [p for p in result["processors"] if p["source"] == "gpu_tray"]
        assert len(host_procs) == 2
        assert len(gpu_procs) == 3

        gpu0 = next(p for p in gpu_procs if p["Id"] == "GPU_0")
        assert gpu0["ProcessorType"] == "GPU"
        assert gpu0["FirmwareVersion"] == "97.00.7A.00.01"
        assert gpu0["MemorySummary"]["TotalSystemMemoryGiB"] == 192

        fpga = next(p for p in gpu_procs if p["Id"] == "FPGA_0")
        assert fpga["ProcessorType"] == "FPGA"
        assert fpga["FirmwareVersion"] == "2.8.0"

    def test_non_b300_no_gpu_tray(self):
        """Standard server without HGX baseboard returns only host processors."""
        responses = {
            "/Systems": (
                {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
                None,
            ),
            "/Processors": (
                {"Members": [{"@odata.id": "/redfish/v1/Systems/1/Processors/CPU0"}]},
                None,
            ),
            "/CPU0": (
                {
                    "Id": "CPU0",
                    "Manufacturer": "Intel",
                    "Model": "Xeon Gold 6430",
                    "TotalCores": 32,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 1
        assert all(p["source"] == "host" for p in result["processors"])
        assert not any(p.get("FirmwareVersion") for p in result["processors"])

    def test_hgx_processors_404_still_returns_host(self):
        """If HGX_Baseboard_0 exists but its Processors endpoint 404s, host CPUs still returned."""
        responses = {
            "/Systems": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
                        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
                    ]
                },
                None,
            ),
            "/System.Embedded.1/Processors": (
                {
                    "Members": [
                        {
                            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Processors/CPU.Socket.1"
                        }
                    ]
                },
                None,
            ),
            "/CPU.Socket.1": (
                {
                    "Id": "CPU.Socket.1",
                    "Manufacturer": "AMD",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/HGX_Baseboard_0/Processors": (None, "404 Not Found"),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/System.Embedded.1"
        )
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 1
        assert result["processors"][0]["source"] == "host"
        assert any("HGX_Baseboard_0" in e for e in result["errors"])

    def test_gpu_memory_summary_absent(self):
        """GPU processors without MemorySummary don't include the field."""
        responses = {
            "/Systems": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
                        {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
                    ]
                },
                None,
            ),
            "/System.Embedded.1/Processors": ({"Members": []}, None),
            "/HGX_Baseboard_0/Processors": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0"}
                    ]
                },
                None,
            ),
            "/GPU_0": (
                {
                    "Id": "GPU_0",
                    "Manufacturer": "NVIDIA",
                    "ProcessorType": "GPU",
                    "FirmwareVersion": "97.00.7A.00.01",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/System.Embedded.1"
        )
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 1
        gpu = result["processors"][0]
        assert gpu["source"] == "gpu_tray"
        assert gpu["FirmwareVersion"] == "97.00.7A.00.01"
        assert "MemorySummary" not in gpu

    def test_systems_endpoint_failure_still_returns_host(self):
        """If /redfish/v1/Systems fails (for HGX discovery), host CPUs still returned."""
        responses = {
            "/Systems": (None, "500 Internal Server Error"),
            "/Processors": (
                {"Members": [{"@odata.id": "/redfish/v1/Systems/1/Processors/CPU0"}]},
                None,
            ),
            "/CPU0": (
                {
                    "Id": "CPU0",
                    "Manufacturer": "Intel",
                    "TotalCores": 32,
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        assert result["count"] == 1
        assert result["processors"][0]["source"] == "host"


class TestCollectMemoryInventory(unittest.TestCase):
    def test_mixed_dimms(self):
        responses = {
            "/Memory": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/1/Memory/DIMM.Socket.A1"},
                        {"@odata.id": "/redfish/v1/Systems/1/Memory/DIMM.Socket.A2"},
                    ]
                },
                None,
            ),
            "DIMM.Socket.A1": (
                {
                    "Id": "DIMM.Socket.A1",
                    "CapacityMiB": 65536,
                    "OperatingSpeedMhz": 4800,
                    "MemoryDeviceType": "DDR5",
                    "Manufacturer": "Samsung",
                    "Status": {"State": "Enabled", "Health": "OK"},
                },
                None,
            ),
            "DIMM.Socket.A2": (
                {
                    "Id": "DIMM.Socket.A2",
                    "CapacityMiB": 0,
                    "Status": {"State": "Absent"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_memory_inventory(c, ep)
        assert result["count"] == 2
        assert result["populated_count"] == 1
        assert result["total_capacity_gib"] == 64.0
        assert result["summary"]["empty_slots"] == 1


class TestCollectPcieInventory(unittest.TestCase):
    def test_gpu_and_nic(self):
        responses = {
            "/PCIeDevices": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/1/PCIeDevices/GPU0"},
                        {"@odata.id": "/redfish/v1/Systems/1/PCIeDevices/NIC1"},
                    ]
                },
                None,
            ),
            "/GPU0": (
                {
                    "Id": "GPU0",
                    "Name": "NVIDIA H100 GPU",
                    "Manufacturer": "NVIDIA",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/NIC1": (
                {
                    "Id": "NIC1",
                    "Name": "Mellanox ConnectX-7 Ethernet",
                    "Manufacturer": "Mellanox",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_pcie_inventory(c, ep)
        assert result["count"] == 2
        assert "gpu" in result["by_type"]
        assert "network" in result["by_type"]
        assert all(d["source"] == "system" for d in result["devices"])


class TestPcieHgxChassisFallback(unittest.TestCase):
    """B300: PCIe devices under HGX_GPU_* / HGX_ConnectX_* chassis members."""

    def test_hgx_fallback_finds_gpu_pcie(self):
        """System-level PCIeDevices empty -> falls back to HGX chassis members."""
        responses = {
            "/PCIeDevices": (None, "404 not found"),
            "/redfish/v1/Systems/System.Embedded.1": ({"PCIeDevices": []}, None),
            "/redfish/v1/Chassis": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0"},
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_1"},
                        {"@odata.id": "/redfish/v1/Chassis/HGX_ConnectX_0"},
                        {"@odata.id": "/redfish/v1/Chassis/ERoT_GPU_0"},
                    ]
                },
                None,
            ),
            "/Chassis/HGX_GPU_0/PCIeDevices": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_0/PCIeDevices/GPU0"},
                    ]
                },
                None,
            ),
            "/PCIeDevices/GPU0": (
                {
                    "Id": "GPU0",
                    "Name": "NVIDIA B200 GPU",
                    "Manufacturer": "NVIDIA",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/Chassis/HGX_GPU_1/PCIeDevices": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/HGX_GPU_1/PCIeDevices/GPU1"},
                    ]
                },
                None,
            ),
            "/PCIeDevices/GPU1": (
                {
                    "Id": "GPU1",
                    "Name": "NVIDIA B200 GPU",
                    "Manufacturer": "NVIDIA",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
            "/Chassis/HGX_ConnectX_0/PCIeDevices": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/HGX_ConnectX_0/PCIeDevices/CX0"},
                    ]
                },
                None,
            ),
            "/PCIeDevices/CX0": (
                {
                    "Id": "CX0",
                    "Name": "Mellanox ConnectX-7",
                    "Manufacturer": "Mellanox",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1",
            system_path="/redfish/v1/Systems/System.Embedded.1",
        )
        result = collect_pcie_inventory(c, ep)
        assert result["count"] == 3
        assert "gpu" in result["by_type"]
        assert "network" in result["by_type"]
        ids = {d["Id"] for d in result["devices"]}
        assert ids == {"GPU0", "GPU1", "CX0"}
        assert all(d["source"] == "hgx_chassis" for d in result["devices"])

    def test_system_pcie_present_skips_hgx_fallback(self):
        """When system-level PCIeDevices returns results, HGX fallback is not used."""
        responses = {
            "/PCIeDevices": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Systems/1/PCIeDevices/HostNIC"},
                    ]
                },
                None,
            ),
            "/HostNIC": (
                {
                    "Id": "HostNIC",
                    "Name": "Intel Ethernet",
                    "Manufacturer": "Intel",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1",
            system_path="/redfish/v1/Systems/System.Embedded.1",
        )
        result = collect_pcie_inventory(c, ep)
        assert result["count"] == 1
        assert result["devices"][0]["Id"] == "HostNIC"
        assert result["devices"][0]["source"] == "system"
        called_urls = [call.args[0] for call in c.get_json_maybe.call_args_list]
        assert not any("/redfish/v1/Chassis" in u for u in called_urls)

    def test_no_pcie_anywhere(self):
        """Neither system-level nor HGX chassis has PCIe devices."""
        responses = {
            "/PCIeDevices": (None, "404 not found"),
            "/redfish/v1/Systems/System.Embedded.1": ({"PCIeDevices": []}, None),
            "/redfish/v1/Chassis": (
                {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Chassis/System.Embedded.1"},
                    ]
                },
                None,
            ),
        }
        c = _mock_client(responses)
        ep = RedfishEndpoint(
            base_url="https://10.0.0.1",
            system_path="/redfish/v1/Systems/System.Embedded.1",
        )
        result = collect_pcie_inventory(c, ep)
        assert result["count"] == 0
        assert result["devices"] == []


class TestCollectManagerInfo(unittest.TestCase):
    def test_basic_manager(self):
        responses = {
            "/redfish/v1/Managers": (
                {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
                None,
            ),
            "/redfish/v1/Managers/1": (
                {
                    "Id": "1",
                    "ManagerType": "BMC",
                    "FirmwareVersion": "4.01.05",
                    "Model": "ATEN",
                    "Status": {"State": "Enabled", "Health": "OK"},
                    "NetworkProtocol": {"@odata.id": "/redfish/v1/Managers/1/NetworkProtocol"},
                },
                None,
            ),
            "/NetworkProtocol": (
                {
                    "HostName": "bmc-host",
                    "SSH": {"ProtocolEnabled": True, "Port": 22},
                    "HTTPS": {"ProtocolEnabled": True, "Port": 443},
                    "IPMI": {"ProtocolEnabled": True, "Port": 623},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        result = collect_manager_info(c)
        assert result["manager"]["FirmwareVersion"] == "4.01.05"
        assert result["manager"]["network_protocols"]["SSH"]["Port"] == 22
        assert result["manager"]["hostname_bmc"] == "bmc-host"


class TestCollectManagerEthernet(unittest.TestCase):
    def test_single_interface(self):
        responses = {
            "/redfish/v1/Managers": (
                {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
                None,
            ),
            "/EthernetInterfaces": (
                {"Members": [{"@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1"}]},
                None,
            ),
            "/EthernetInterfaces/1": (
                {
                    "Id": "1",
                    "MACAddress": "AA:BB:CC:DD:EE:FF",
                    "IPv4Addresses": [
                        {
                            "Address": "192.168.196.12",
                            "SubnetMask": "255.255.255.0",
                            "Gateway": "192.168.196.1",
                            "AddressOrigin": "Static",
                        }
                    ],
                    "LinkStatus": "LinkUp",
                    "Status": {"Health": "OK"},
                },
                None,
            ),
        }
        c = _mock_client(responses)
        result = collect_manager_ethernet(c)
        assert result["count"] == 1
        iface = result["interfaces"][0]
        assert iface["MACAddress"] == "AA:BB:CC:DD:EE:FF"
        assert iface["IPv4Addresses"][0]["Address"] == "192.168.196.12"


if __name__ == "__main__":
    unittest.main()
