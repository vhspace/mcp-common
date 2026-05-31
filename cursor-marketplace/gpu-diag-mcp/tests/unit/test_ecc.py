"""Tests for ECC error parsing with real diagnostic output from research-common-h100."""

import pytest

from gpu_diag_mcp.parsers.ecc import parse_ecc_csv
from tests.conftest import load_fixture


class TestCleanEcc:
    """Node001 has zero ECC errors across all GPUs."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_ecc_csv(load_fixture("node001_ecc.txt"))

    def test_parses_eight_gpus(self):
        assert len(self.result["gpus"]) == 8

    def test_severity_ok(self):
        assert self.result["severity"] == "ok"

    def test_all_zeros(self):
        for gpu in self.result["gpus"]:
            assert gpu["volatile_correctable"] == 0
            assert gpu["volatile_uncorrectable"] == 0
            assert gpu["aggregate_correctable"] == 0
            assert gpu["aggregate_uncorrectable"] == 0

    def test_summary_totals_zero(self):
        assert self.result["summary"]["total_correctable"] == 0
        assert self.result["summary"]["total_uncorrectable"] == 0

    def test_no_flags(self):
        assert self.result["summary"]["any_volatile_uncorrectable"] is False
        assert self.result["summary"]["any_aggregate_uncorrectable"] is False
        assert self.result["summary"]["high_aggregate_correctable"] is False


class TestUncorrectableEcc:
    """Synthetic data with volatile uncorrectable errors → critical."""

    ECC_WITH_UNCORR = """\
index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total
0, 0, 3, 0, 0
1, 0, 0, 0, 0
2, 0, 0, 0, 5
3, 0, 0, 0, 0
"""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_ecc_csv(self.ECC_WITH_UNCORR)

    def test_severity_critical(self):
        assert self.result["severity"] == "critical"

    def test_volatile_uncorrectable_flagged(self):
        assert self.result["summary"]["any_volatile_uncorrectable"] is True

    def test_aggregate_uncorrectable_flagged(self):
        assert self.result["summary"]["any_aggregate_uncorrectable"] is True

    def test_total_uncorrectable(self):
        assert self.result["summary"]["total_uncorrectable"] == 8


class TestHighCorrectableEcc:
    """Synthetic data with high aggregate correctable errors → warning."""

    ECC_HIGH_CORR = """\
index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total
0, 0, 0, 1500, 0
1, 0, 0, 0, 0
"""

    def test_severity_warning(self):
        result = parse_ecc_csv(self.ECC_HIGH_CORR)
        assert result["severity"] == "warning"
        assert result["summary"]["high_aggregate_correctable"] is True


class TestEmptyEcc:
    def test_empty_string(self):
        result = parse_ecc_csv("")
        assert result["gpus"] == []
        assert result["severity"] == "ok"

    def test_header_only(self):
        header = "index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total\n"
        result = parse_ecc_csv(header)
        assert result["gpus"] == []
        assert result["severity"] == "ok"
