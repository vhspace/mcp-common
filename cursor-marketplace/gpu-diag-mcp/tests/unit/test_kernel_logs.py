"""Tests for kernel log parsing with real diagnostic output from research-common-h100."""

import pytest

from gpu_diag_mcp.parsers.kernel_logs import parse_kernel_xid_logs
from tests.conftest import load_fixture


class TestXidParsing:
    """Parse XID events from node001 kernel logs."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_kernel_xid_logs(load_fixture("node001_kernel_logs.txt"))

    def test_finds_four_xid_events(self):
        assert len(self.result["xid_events"]) == 4

    def test_all_xid_code_94(self):
        for event in self.result["xid_events"]:
            assert event["xid_code"] == 94

    def test_xid_events_have_pid(self):
        for event in self.result["xid_events"]:
            assert event["pid"] is not None
            assert isinstance(event["pid"], int)

    def test_xid_events_have_process_name(self):
        for event in self.result["xid_events"]:
            assert event["process_name"] == "python3"

    def test_xid_events_have_pci_bus(self):
        pci_buses = {e["pci_bus"] for e in self.result["xid_events"]}
        assert "0000:04:00" in pci_buses
        assert "0000:e4:00" in pci_buses

    def test_unique_xid_codes(self):
        assert self.result["summary"]["unique_xid_codes"] == [94]

    def test_severity_critical_with_xids(self):
        assert self.result["severity"] == "critical"


class TestSxidParsing:
    """Parse SXid events from node001 kernel logs."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_kernel_xid_logs(load_fixture("node001_kernel_logs.txt"))

    def test_finds_sxid_events(self):
        assert len(self.result["sxid_events"]) == 3

    def test_sxid_code_12028(self):
        for event in self.result["sxid_events"]:
            assert event["sxid_code"] == 12028

    def test_unique_sxid_codes(self):
        assert self.result["summary"]["unique_sxid_codes"] == [12028]

    def test_sxid_severity_field_parsed(self):
        severity_0_events = [e for e in self.result["sxid_events"] if e["severity"] == "0"]
        assert len(severity_0_events) >= 1


class TestAssertFailures:
    """Parse assertion failures from node001 kernel logs."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_kernel_xid_logs(load_fixture("node001_kernel_logs.txt"))

    def test_finds_assert_failure(self):
        assert len(self.result["assert_failures"]) == 1

    def test_assert_gpu_index(self):
        assert self.result["assert_failures"][0]["gpu_index"] == 2

    def test_assert_message(self):
        assertion = self.result["assert_failures"][0]["assertion"]
        assert "pRecord->idx == reqIdx" in assertion


class TestFbhubBootTime:
    """Parse FBHUB interrupts from node090 — expected boot-time pattern."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.result = parse_kernel_xid_logs(load_fixture("node090_fbhub.txt"))

    def test_finds_eight_fbhub_events(self):
        assert len(self.result["fbhub_events"]) == 8

    def test_is_not_boot_time_fbhub_span_exceeds_window(self):
        # 11:29:10 to 11:29:23 = 13s, exceeds the 10s boot-time window
        assert self.result["summary"]["is_boot_time_fbhub"] is False

    def test_gpu_indices_0_through_7(self):
        indices = sorted(e["gpu_index"] for e in self.result["fbhub_events"])
        assert indices == list(range(8))

    def test_severity_warning_for_non_boot_fbhub(self):
        assert self.result["severity"] == "warning"


class TestFbhubBootTimeTight:
    """Synthetic FBHUB events within the 10-second window → boot-time pattern."""

    TIGHT_FBHUB = "\n".join(
        f"Apr 02 11:29:1{i} host kernel: NVRM: GPU{i} gpuClearFbhubPoisonIntrForBug2924523_GA100: FBHUB Interrupt detected. Clearing it."
        for i in range(8)
    )

    def test_is_boot_time_fbhub(self):
        result = parse_kernel_xid_logs(self.TIGHT_FBHUB)
        assert result["summary"]["is_boot_time_fbhub"] is True

    def test_severity_ok(self):
        result = parse_kernel_xid_logs(self.TIGHT_FBHUB)
        assert result["severity"] == "ok"


class TestEmptyInput:
    def test_empty_string(self):
        result = parse_kernel_xid_logs("")
        assert result["xid_events"] == []
        assert result["sxid_events"] == []
        assert result["fbhub_events"] == []
        assert result["assert_failures"] == []
        assert result["severity"] == "ok"

    def test_whitespace_only(self):
        result = parse_kernel_xid_logs("   \n\n  ")
        assert result["xid_events"] == []
        assert result["severity"] == "ok"

    def test_no_matches(self):
        result = parse_kernel_xid_logs("Apr 02 01:00:00 host kernel: normal log line\n")
        assert result["xid_events"] == []
        assert result["sxid_events"] == []
        assert result["severity"] == "ok"
