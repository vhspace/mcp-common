"""Tests for NCCL result parsing with real output from research-common-h100."""

import pytest

from gpu_diag_mcp.parsers.nccl import parse_nccl_results
from tests.conftest import load_fixture


class TestBootstrapHang:
    """Node081 hung at NCCL bootstrap — OMP_NUM_THREADS warning with no NCCL init."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_nccl_results(load_fixture("nccl_bootstrap_hang.txt"))

    def test_severity_critical(self):
        assert self.result["severity"] == "critical"

    def test_bootstrap_hang_detected(self):
        assert "bootstrap_hang" in self.result["failures"]

    def test_not_successful(self):
        assert self.result["success"] is False

    def test_no_data_rows(self):
        assert self.result["data_rows"] == []

    def test_init_not_complete(self):
        assert self.result["init_complete"] is False


class TestPeerWaiting:
    """Node081 stuck waiting for master peer."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_nccl_results(load_fixture("nccl_peer_waiting.txt"))

    def test_peer_waiting_detected(self):
        assert "peer_waiting" in self.result["failures"]

    def test_not_successful(self):
        assert self.result["success"] is False

    def test_waiting_for_address(self):
        assert any("192.168.229.81:29600" in addr for addr in self.result["waiting_for"])


class TestEmptyNccl:
    def test_empty_string(self):
        result = parse_nccl_results("")
        assert result["success"] is False
        assert result["severity"] == "critical"
        assert "no_output" in result["failures"]

    def test_whitespace_only(self):
        result = parse_nccl_results("  \n  ")
        assert result["success"] is False

    def test_empty_preserves_expected_gpus(self):
        result = parse_nccl_results("", expected_gpus=4)
        assert result["expected_gpus"] == 4

    def test_empty_preserves_expected_min_bw(self):
        result = parse_nccl_results("", expected_min_bw=200.0)
        assert result["expected_min_bw"] == 200.0


class TestSuccessfulNccl:
    """Synthetic successful NCCL run with bandwidth above threshold."""

    GOOD_NCCL = """\
#
#                                                              out-of-place                       in-place
#       size         count      type   redop    root     time   algbw   busbw  #wrong     time   algbw   busbw  #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
NCCL INFO Init COMPLETE
     1048576        262144     float     sum       0    123.4   8.50  370.00       0    123.4   8.50  370.00       0
# Avg bus bandwidth    : 370.00
"""

    def test_success(self):
        result = parse_nccl_results(self.GOOD_NCCL)
        assert result["success"] is True
        assert result["severity"] == "ok"

    def test_avg_busbw(self):
        result = parse_nccl_results(self.GOOD_NCCL)
        assert result["avg_busbw"] == 370.0

    def test_init_complete(self):
        result = parse_nccl_results(self.GOOD_NCCL)
        assert result["init_complete"] is True

    def test_no_failures(self):
        result = parse_nccl_results(self.GOOD_NCCL)
        assert result["failures"] == []

    def test_gb200_expected_gpus(self):
        result = parse_nccl_results(self.GOOD_NCCL, expected_gpus=4)
        assert result["expected_gpus"] == 4
        assert result["success"] is True
