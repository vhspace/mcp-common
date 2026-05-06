"""Tests for IB device parsing with real ibdev2netdev output from research-common-h100."""

import pytest

from gpu_diag_mcp.parsers.ibstat import (
    GB200_ETH_DEVICES,
    GB200_IB_DEVICES,
    H100_IB_DEVICES,
    NODE_TOPOLOGIES,
    parse_ibdev2netdev,
)
from tests.conftest import load_fixture


class TestAllPortsUp:
    """Node001: all 10 ports (8 IB + 2 Ethernet) are up."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_ibdev2netdev(load_fixture("node001_ibdev2netdev.txt"))

    def test_all_ib_up(self):
        assert self.result["all_ib_up"] is True

    def test_severity_ok(self):
        assert self.result["severity"] == "ok"

    def test_no_ports_down(self):
        assert self.result["ports_down"] == []

    def test_no_missing_ib(self):
        assert self.result["missing_ib_devices"] == []

    def test_no_missing_eth(self):
        assert self.result["missing_eth_devices"] == []

    def test_ten_devices(self):
        assert len(self.result["devices"]) == 10

    def test_ports_up_count(self):
        assert self.result["ports_up_count"] == 10


class TestOnePortDown:
    """Node081: mlx5_6 (IB port ibp196s0) is down."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_ibdev2netdev(load_fixture("node081_ibdev2netdev.txt"))

    def test_all_ib_up_false(self):
        assert self.result["all_ib_up"] is False

    def test_severity_critical(self):
        assert self.result["severity"] == "critical"

    def test_ports_down_contains_mlx5_6(self):
        assert "mlx5_6" in self.result["ports_down"]

    def test_one_port_down(self):
        assert len(self.result["ports_down"]) == 1

    def test_missing_ib_includes_mlx5_6(self):
        assert "mlx5_6" in self.result["missing_ib_devices"]

    def test_ports_up_count(self):
        assert self.result["ports_up_count"] == 9


class TestGB200Topology:
    """GB200 topology: 4 IB + 1 Ethernet."""

    GB200_ALL_UP = """\
mlx5_0 port 1 ==> ibs1 (Up)
mlx5_1 port 1 ==> ibs2 (Up)
mlx5_2 port 1 ==> ibs3 (Up)
mlx5_3 port 1 ==> ibs4 (Up)
mlx5_4 port 1 ==> eth0 (Up)
"""

    GB200_ONE_DOWN = """\
mlx5_0 port 1 ==> ibs1 (Up)
mlx5_1 port 1 ==> ibs2 (Down)
mlx5_2 port 1 ==> ibs3 (Up)
mlx5_3 port 1 ==> ibs4 (Up)
mlx5_4 port 1 ==> eth0 (Up)
"""

    def test_gb200_all_up(self):
        topo = NODE_TOPOLOGIES["gb200"]
        result = parse_ibdev2netdev(
            self.GB200_ALL_UP,
            expected_ib_devices=topo["ib"],
            expected_eth_devices=topo["eth"],
        )
        assert result["all_ib_up"] is True
        assert result["severity"] == "ok"
        assert result["expected_ib_count"] == 4
        assert result["expected_eth_count"] == 1

    def test_gb200_one_port_down(self):
        topo = NODE_TOPOLOGIES["gb200"]
        result = parse_ibdev2netdev(
            self.GB200_ONE_DOWN,
            expected_ib_devices=topo["ib"],
            expected_eth_devices=topo["eth"],
        )
        assert result["all_ib_up"] is False
        assert result["severity"] == "critical"
        assert "mlx5_1" in result["ports_down"]

    def test_gb200_would_fail_with_h100_topology(self):
        result = parse_ibdev2netdev(self.GB200_ALL_UP)
        assert result["all_ib_up"] is False
        assert len(result["missing_ib_devices"]) > 0

    def test_topology_presets(self):
        assert len(GB200_IB_DEVICES) == 4
        assert len(GB200_ETH_DEVICES) == 1
        assert len(H100_IB_DEVICES) == 8


class TestEmptyIbdev:
    def test_empty_string(self):
        result = parse_ibdev2netdev("")
        assert result["all_ib_up"] is False
        assert result["severity"] == "critical"
        assert result["devices"] == []

    def test_whitespace_only(self):
        result = parse_ibdev2netdev("  \n  ")
        assert result["all_ib_up"] is False

    def test_empty_gb200(self):
        topo = NODE_TOPOLOGIES["gb200"]
        result = parse_ibdev2netdev(
            "",
            expected_ib_devices=topo["ib"],
            expected_eth_devices=topo["eth"],
        )
        assert result["expected_ib_count"] == 4
        assert result["expected_eth_count"] == 1
        assert len(result["missing_ib_devices"]) == 4
