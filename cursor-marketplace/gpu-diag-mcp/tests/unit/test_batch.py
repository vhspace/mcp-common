"""Tests for batch multi-host diagnostic parsing."""

from unittest.mock import patch

import pytest

from gpu_diag_mcp.parsers.batch import NODE_HEADER_RE, parse_batch, split_nodes

# ---------------------------------------------------------------------------
# Helpers — reusable snippets for building multi-host output
# ---------------------------------------------------------------------------

_HEALTHY_IB = """\
mlx5_0 port 1 ==> ibs3 (Up)
mlx5_1 port 1 ==> ibs2 (Up)
mlx5_2 port 1 ==> ibs1 (Up)
mlx5_3 port 1 ==> ibs4 (Up)
mlx5_4 port 1 ==> ens1f0np0 (Up)
mlx5_5 port 1 ==> ibs5 (Up)
mlx5_6 port 1 ==> ibs6 (Up)
mlx5_7 port 1 ==> ens2f0np0 (Up)
mlx5_8 port 1 ==> ibs7 (Up)
mlx5_9 port 1 ==> ibs8 (Up)
"""

_IB_ONE_DOWN = """\
mlx5_0 port 1 ==> ibs3 (Up)
mlx5_1 port 1 ==> ibs2 (Up)
mlx5_2 port 1 ==> ibs1 (Down)
mlx5_3 port 1 ==> ibs4 (Up)
mlx5_4 port 1 ==> ens1f0np0 (Up)
mlx5_5 port 1 ==> ibs5 (Up)
mlx5_6 port 1 ==> ibs6 (Up)
mlx5_7 port 1 ==> ens2f0np0 (Up)
mlx5_8 port 1 ==> ibs7 (Up)
mlx5_9 port 1 ==> ibs8 (Up)
"""

_HEALTHY_ECC = """\
index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total
0, 0, 0, 0, 0
1, 0, 0, 0, 0
"""

_ECC_WITH_UNCORRECTABLE = """\
index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total
0, 0, 3, 0, 0
1, 0, 0, 0, 0
"""

_ECC_HIGH_CORRECTABLE = """\
index, ecc.errors.corrected.volatile.total, ecc.errors.uncorrected.volatile.total, ecc.errors.corrected.aggregate.total, ecc.errors.uncorrected.aggregate.total
0, 0, 0, 1500, 0
1, 0, 0, 0, 0
"""

_HEALTHY_RETIRED = """\
gpu_uuid, retired_pages.address, retired_pages.cause
GPU-aaa, 0x0001, Single Bit ECC
GPU-aaa, 0x0002, Double Bit ECC
GPU-bbb, 0x0003, Single Bit ECC
GPU-bbb, 0x0004, Double Bit ECC
"""


def _build_multihost(*nodes: tuple[str, dict[str, str]]) -> str:
    """Build multi-host output from (hostname, {section: content}) tuples."""
    parts: list[str] = []
    for hostname, sections in nodes:
        parts.append(f"--- {hostname} ---")
        for tag, content in sections.items():
            parts.append(f"={tag.upper()}=")
            parts.append(content)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_string(self):
        result = parse_batch("")
        assert result["nodes"] == []
        assert result["summary"]["total_nodes"] == 0
        assert result["severity"] == "ok"

    def test_whitespace_only(self):
        result = parse_batch("  \n  \n  ")
        assert result["nodes"] == []
        assert result["severity"] == "ok"

    def test_none_like_empty(self):
        result = parse_batch("")
        assert result["worst_nodes"] == []


class TestSingleNodeAllSections:
    """Single node with all six sections, all healthy."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        text = _build_multihost(
            (
                "node-036",
                {
                    "ib": _HEALTHY_IB,
                    "ecc": _HEALTHY_ECC,
                    "retired": _HEALTHY_RETIRED,
                    "kernel": "",
                    "nvlink": "",
                },
            ),
        )
        self.result = parse_batch(text)

    def test_one_node(self):
        assert self.result["summary"]["total_nodes"] == 1

    def test_overall_ok(self):
        assert self.result["severity"] == "ok"

    def test_node_severity_ok(self):
        assert self.result["nodes"][0]["overall_severity"] == "ok"

    def test_ib_check_present(self):
        checks = self.result["nodes"][0]["checks"]
        assert "ib" in checks
        assert checks["ib"]["severity"] == "ok"
        assert checks["ib"]["all_ib_up"] is True

    def test_ecc_check_present(self):
        checks = self.result["nodes"][0]["checks"]
        assert "ecc" in checks
        assert checks["ecc"]["severity"] == "ok"

    def test_retired_pages_check(self):
        checks = self.result["nodes"][0]["checks"]
        assert "retired_pages" in checks
        assert checks["retired_pages"]["severity"] == "ok"

    def test_worst_nodes_empty(self):
        assert self.result["worst_nodes"] == []


class TestMultipleNodesVaryingHealth:
    """Multiple nodes: some healthy, one with IB down (critical)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        text = _build_multihost(
            ("research-common-h100-036", {"ib": _HEALTHY_IB, "ecc": _HEALTHY_ECC}),
            ("research-common-h100-041", {"ib": _HEALTHY_IB, "ecc": _HEALTHY_ECC}),
            ("research-common-h100-089", {"ib": _IB_ONE_DOWN, "ecc": _HEALTHY_ECC}),
        )
        self.result = parse_batch(text)

    def test_three_nodes(self):
        assert self.result["summary"]["total_nodes"] == 3

    def test_overall_critical(self):
        assert self.result["severity"] == "critical"

    def test_one_critical(self):
        assert self.result["summary"]["critical"] == 1

    def test_two_ok(self):
        assert self.result["summary"]["ok"] == 2

    def test_critical_node_first(self):
        assert self.result["nodes"][0]["node"] == "research-common-h100-089"
        assert self.result["nodes"][0]["overall_severity"] == "critical"

    def test_worst_nodes(self):
        assert self.result["worst_nodes"] == ["research-common-h100-089"]

    def test_ib_port_down_in_critical_node(self):
        checks = self.result["nodes"][0]["checks"]
        assert checks["ib"]["severity"] == "critical"
        assert "mlx5_2" in checks["ib"]["ports_down"]


class TestMissingSections:
    """Nodes with only partial sections should still parse."""

    def test_only_ib_data(self):
        text = _build_multihost(
            ("node-001", {"ib": _HEALTHY_IB}),
            ("node-002", {"ib": _IB_ONE_DOWN}),
        )
        result = parse_batch(text)
        assert result["summary"]["total_nodes"] == 2
        assert result["severity"] == "critical"

        checks_001 = result["nodes"][-1]["checks"]
        assert "ib" in checks_001
        assert "ecc" not in checks_001

    def test_only_ecc_data(self):
        text = _build_multihost(
            ("node-001", {"ecc": _HEALTHY_ECC}),
        )
        result = parse_batch(text)
        assert result["summary"]["total_nodes"] == 1
        checks = result["nodes"][0]["checks"]
        assert "ecc" in checks
        assert "ib" not in checks


class TestCriticalIBRisesToTop:
    """A node with critical IB should sort before warning-only nodes."""

    def test_ib_down_beats_ecc_warning(self):
        text = _build_multihost(
            ("alpha", {"ib": _HEALTHY_IB, "ecc": _ECC_HIGH_CORRECTABLE}),
            ("beta", {"ib": _IB_ONE_DOWN, "ecc": _HEALTHY_ECC}),
            ("gamma", {"ib": _HEALTHY_IB, "ecc": _HEALTHY_ECC}),
        )
        result = parse_batch(text)
        assert result["nodes"][0]["node"] == "beta"
        assert result["nodes"][0]["overall_severity"] == "critical"
        assert result["nodes"][1]["node"] == "alpha"
        assert result["nodes"][1]["overall_severity"] == "warning"


class TestCaseInsensitiveSectionMarkers:
    """Section markers should work regardless of case."""

    def test_uppercase_markers(self):
        text = "--- node-001 ---\n=IB=\n" + _HEALTHY_IB
        result = parse_batch(text)
        assert "ib" in result["nodes"][0]["checks"]

    def test_lowercase_markers(self):
        text = "--- node-001 ---\n=ib=\n" + _HEALTHY_IB
        result = parse_batch(text)
        assert "ib" in result["nodes"][0]["checks"]

    def test_mixed_case_markers(self):
        text = "--- node-001 ---\n=Ib=\n" + _HEALTHY_IB
        result = parse_batch(text)
        assert "ib" in result["nodes"][0]["checks"]

    def test_ecc_case_insensitive(self):
        text = "--- node-001 ---\n=ecc=\n" + _HEALTHY_ECC
        result = parse_batch(text)
        assert "ecc" in result["nodes"][0]["checks"]


class TestGB200NodeType:
    """GB200 node_type passes through to IB topology expectations."""

    GB200_HEALTHY_IB = """\
mlx5_0 port 1 ==> ibs1 (Up)
mlx5_1 port 1 ==> ibs2 (Up)
mlx5_2 port 1 ==> ibs3 (Up)
mlx5_3 port 1 ==> ibs4 (Up)
mlx5_4 port 1 ==> eth0 (Up)
"""

    def test_gb200_all_up(self):
        text = "--- gb200-node ---\n=IB=\n" + self.GB200_HEALTHY_IB
        result = parse_batch(text, node_type="gb200")
        checks = result["nodes"][0]["checks"]
        assert checks["ib"]["severity"] == "ok"
        assert checks["ib"]["all_ib_up"] is True

    def test_gb200_would_fail_as_h100(self):
        text = "--- gb200-node ---\n=IB=\n" + self.GB200_HEALTHY_IB
        result = parse_batch(text, node_type="h100")
        checks = result["nodes"][0]["checks"]
        assert checks["ib"]["severity"] == "critical"
        assert checks["ib"]["all_ib_up"] is False


class TestMixedHealthScenario:
    """Complex scenario: healthy, ECC errors, IB down across multiple nodes."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        text = _build_multihost(
            ("healthy-001", {"ib": _HEALTHY_IB, "ecc": _HEALTHY_ECC}),
            ("healthy-002", {"ib": _HEALTHY_IB, "ecc": _HEALTHY_ECC}),
            ("ecc-bad-003", {"ib": _HEALTHY_IB, "ecc": _ECC_WITH_UNCORRECTABLE}),
            ("ib-down-004", {"ib": _IB_ONE_DOWN, "ecc": _HEALTHY_ECC}),
            ("healthy-005", {"ib": _HEALTHY_IB}),
        )
        self.result = parse_batch(text)

    def test_five_nodes(self):
        assert self.result["summary"]["total_nodes"] == 5

    def test_overall_critical(self):
        assert self.result["severity"] == "critical"

    def test_critical_count(self):
        assert self.result["summary"]["critical"] == 2

    def test_ok_count(self):
        assert self.result["summary"]["ok"] == 3

    def test_ib_down_node_in_worst(self):
        assert "ib-down-004" in self.result["worst_nodes"]

    def test_ecc_critical_node_in_worst(self):
        assert "ecc-bad-003" in self.result["worst_nodes"]

    def test_critical_nodes_sorted_first(self):
        first_two = [n["node"] for n in self.result["nodes"][:2]]
        assert "ib-down-004" in first_two
        assert "ecc-bad-003" in first_two


class TestNodeHeaderRegex:
    """Verify _NODE_HEADER_RE matches expected formats."""

    def test_dashes_format(self):
        m = NODE_HEADER_RE.match("--- research-common-h100-036 ---")
        assert m
        hostname = next(g for g in m.groups() if g)
        assert hostname == "research-common-h100-036"

    def test_equals_format(self):
        m = NODE_HEADER_RE.match("=== node001 ===")
        assert m

    def test_hash_format(self):
        m = NODE_HEADER_RE.match("### my-hostname")
        assert m
        hostname = next(g for g in m.groups() if g)
        assert hostname == "my-hostname"

    def test_fqdn_format(self):
        m = NODE_HEADER_RE.match("node001.cloud.together.ai")
        assert m

    def test_no_match_for_data(self):
        m = NODE_HEADER_RE.match("mlx5_0 port 1 ==> ibs3 (Up)")
        assert m is None


class TestCompactOutput:
    """Verify output is compact — no full parser data dumped."""

    def test_ib_check_compact(self):
        text = "--- node ---\n=IB=\n" + _HEALTHY_IB
        result = parse_batch(text)
        ib = result["nodes"][0]["checks"]["ib"]
        assert "devices" not in ib
        assert "severity" in ib
        assert "all_ib_up" in ib

    def test_ecc_check_compact(self):
        text = "--- node ---\n=ECC=\n" + _HEALTHY_ECC
        result = parse_batch(text)
        ecc_check = result["nodes"][0]["checks"]["ecc"]
        assert "gpus" not in ecc_check
        assert "severity" in ecc_check
        assert "total_uncorrectable" in ecc_check

    def test_retired_pages_check_compact(self):
        text = "--- node ---\n=RETIRED=\n" + _HEALTHY_RETIRED
        result = parse_batch(text)
        rp = result["nodes"][0]["checks"]["retired_pages"]
        assert "gpus" not in rp
        assert "severity" in rp
        assert "total_retired" in rp
        assert "is_normal_baseline" in rp


class TestSummaryStructure:
    """Verify summary keys and structure."""

    def test_summary_keys(self):
        text = _build_multihost(("node", {"ib": _HEALTHY_IB}))
        result = parse_batch(text)
        summary = result["summary"]
        assert "total_nodes" in summary
        assert "critical" in summary
        assert "warning" in summary
        assert "ok" in summary

    def test_top_level_keys(self):
        result = parse_batch("")
        assert "nodes" in result
        assert "summary" in result
        assert "worst_nodes" in result
        assert "severity" in result


class TestExceptionResilience:
    """One bad section must not crash the entire batch."""

    def test_bad_ecc_section_still_returns_ib(self):
        text = _build_multihost(
            ("node1", {"ib": _HEALTHY_IB, "ecc": "total garbage\nnot csv at all\n{{{{"})
        )
        with patch("gpu_diag_mcp.parsers.batch.ecc.parse_ecc_csv", side_effect=ValueError("boom")):
            result = parse_batch(text)
        assert len(result["nodes"]) == 1
        checks = result["nodes"][0]["checks"]
        assert checks["ib"]["severity"] == "ok"
        assert checks["ecc"]["severity"] == "unknown"
        assert "error" in checks["ecc"]

    def test_bad_section_other_nodes_unaffected(self):
        text = _build_multihost(
            ("good-node", {"ib": _HEALTHY_IB}),
            ("bad-node", {"ib": _HEALTHY_IB, "ecc": "garbage"}),
        )
        with patch(
            "gpu_diag_mcp.parsers.batch.ecc.parse_ecc_csv", side_effect=RuntimeError("explode")
        ):
            result = parse_batch(text)
        assert result["summary"]["total_nodes"] == 2
        good = next(n for n in result["nodes"] if n["node"] == "good-node")
        bad = next(n for n in result["nodes"] if n["node"] == "bad-node")
        assert good["checks"]["ib"]["severity"] == "ok"
        assert bad["checks"]["ecc"]["severity"] == "unknown"

    def test_all_parsers_fail_returns_unknown(self):
        text = _build_multihost(("node1", {"ib": "x", "ecc": "x", "retired": "x"}))
        with (
            patch(
                "gpu_diag_mcp.parsers.batch.ibstat.parse_ibdev2netdev",
                side_effect=Exception("ib fail"),
            ),
            patch(
                "gpu_diag_mcp.parsers.batch.ecc.parse_ecc_csv", side_effect=Exception("ecc fail")
            ),
            patch(
                "gpu_diag_mcp.parsers.batch.retired_pages.parse_retired_pages",
                side_effect=Exception("rp fail"),
            ),
        ):
            result = parse_batch(text)
        checks = result["nodes"][0]["checks"]
        assert all(c["severity"] == "unknown" for c in checks.values())


class TestSplitNodesPublic:
    """Verify split_nodes is usable as a public API."""

    def test_split_two_nodes(self):
        text = "--- h100-036 ---\ndata1\n--- h100-041 ---\ndata2\n"
        nodes = split_nodes(text)
        assert "h100-036" in nodes
        assert "h100-041" in nodes
        assert "data1" in nodes["h100-036"]
        assert "data2" in nodes["h100-041"]

    def test_empty_returns_empty(self):
        assert split_nodes("") == {}
