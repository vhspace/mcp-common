"""Tests for retired pages parsing with real output from research-common-h100."""

import pytest

from gpu_diag_mcp.parsers.retired_pages import (
    H100_NORMAL_BASELINE,
    NORMAL_PER_GPU,
    parse_retired_pages,
)
from tests.conftest import load_fixture


class TestNormalBaseline:
    """Node001: 8 GPUs x 2 pages each (1 SBE + 1 DBE) = 16 total — normal H100 baseline."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_retired_pages(load_fixture("node001_retired_pages.txt"))

    def test_total_pages(self):
        assert self.result["summary"]["total_retired"] == 16

    def test_normal_baseline(self):
        assert self.result["summary"]["is_normal_baseline"] is True

    def test_normal_baseline_value(self):
        assert self.result["summary"]["normal_baseline"] == 16

    def test_severity_ok(self):
        assert self.result["severity"] == "ok"

    def test_eight_gpus(self):
        assert self.result["summary"]["gpu_count"] == 8

    def test_per_gpu_counts(self):
        for gpu in self.result["gpus"]:
            assert gpu["single_bit_ecc"] == 1
            assert gpu["double_bit_ecc"] == 1
            assert gpu["total"] == 2

    def test_total_sbe_dbe(self):
        assert self.result["summary"]["total_single_bit"] == 8
        assert self.result["summary"]["total_double_bit"] == 8

    def test_baseline_constant(self):
        assert H100_NORMAL_BASELINE == 16


class TestExcessiveRetiredPages:
    """Synthetic data: one GPU has extra retired pages beyond baseline."""

    EXCESS_PAGES = """\
gpu_uuid, retired_pages.address, retired_pages.cause
GPU-aaa, [N/A], Single Bit ECC
GPU-aaa, [N/A], Double Bit ECC
GPU-aaa, [N/A], Double Bit ECC
GPU-aaa, [N/A], Double Bit ECC
GPU-bbb, [N/A], Single Bit ECC
GPU-bbb, [N/A], Double Bit ECC
"""

    def test_not_normal_baseline(self):
        result = parse_retired_pages(self.EXCESS_PAGES)
        assert result["summary"]["is_normal_baseline"] is False

    def test_severity_not_ok(self):
        result = parse_retired_pages(self.EXCESS_PAGES)
        assert result["severity"] in ("warning", "critical")

    def test_total_dbe_count(self):
        result = parse_retired_pages(self.EXCESS_PAGES)
        assert result["summary"]["total_double_bit"] == 4


class TestGB200RetiredPages:
    """GB200 with 4 GPUs: baseline is 8 total retired pages (2 per GPU)."""

    GB200_NORMAL = """\
gpu_uuid, retired_pages.address, retired_pages.cause
GPU-001, [N/A], Single Bit ECC
GPU-001, [N/A], Double Bit ECC
GPU-002, [N/A], Single Bit ECC
GPU-002, [N/A], Double Bit ECC
GPU-003, [N/A], Single Bit ECC
GPU-003, [N/A], Double Bit ECC
GPU-004, [N/A], Single Bit ECC
GPU-004, [N/A], Double Bit ECC
"""

    def test_gb200_normal_baseline(self):
        result = parse_retired_pages(self.GB200_NORMAL, expected_gpu_count=4)
        assert result["summary"]["is_normal_baseline"] is True
        assert result["summary"]["normal_baseline"] == 8
        assert result["severity"] == "ok"

    def test_gb200_auto_detect_baseline(self):
        result = parse_retired_pages(self.GB200_NORMAL)
        assert result["summary"]["gpu_count"] == 4
        assert result["summary"]["normal_baseline"] == 8
        assert result["summary"]["is_normal_baseline"] is True

    def test_gb200_excess_pages(self):
        excess = self.GB200_NORMAL + "GPU-001, [N/A], Double Bit ECC\n"
        result = parse_retired_pages(excess, expected_gpu_count=4)
        assert result["summary"]["is_normal_baseline"] is False
        assert result["severity"] in ("warning", "critical")

    def test_baseline_formula(self):
        assert NORMAL_PER_GPU == 2
        assert 4 * NORMAL_PER_GPU == 8
        assert 8 * NORMAL_PER_GPU == 16


class TestEmptyRetiredPages:
    def test_empty_string(self):
        result = parse_retired_pages("")
        assert result["gpus"] == []
        assert result["summary"]["total_retired"] == 0
        assert result["severity"] == "ok"

    def test_header_only(self):
        result = parse_retired_pages("gpu_uuid, retired_pages.address, retired_pages.cause\n")
        assert result["gpus"] == []
        assert result["severity"] == "ok"
