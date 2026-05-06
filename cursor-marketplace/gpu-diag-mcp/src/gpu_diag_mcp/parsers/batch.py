"""Parse multi-host diagnostic output with node headers and section markers.

Splits combined output by ``--- hostname ---`` node headers, then routes
``=SECTION=`` blocks to the appropriate single-node parsers.  Returns a
per-node severity-ranked summary suitable for fleet-wide triage.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from gpu_diag_mcp.parsers import ecc, ibstat, kernel_logs, nccl, nvlink, retired_pages

log = logging.getLogger("gpu_diag_mcp")

# ---------------------------------------------------------------------------
# Shared regex for node headers (factored out of server.py)
# ---------------------------------------------------------------------------

NODE_HEADER_RE = re.compile(
    r"^[-=]+\s*(?:node|host)?[:\s]*(\S+)|^(\S+\.(?:cloud|together)\.\S+)\s*$|^###?\s*(\S+)",
    re.IGNORECASE,
)

# Section markers: =IB=, =ECC=, =RETIRED=, =KERNEL=, =NVLINK=, =NCCL=
_SECTION_RE = re.compile(r"^=(\w+)=\s*$", re.IGNORECASE)

_SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}

# Map section tag (lowercased) → parser dispatch key
_SECTION_TAGS = frozenset({"ib", "ecc", "retired", "kernel", "nvlink", "nccl"})


def parse_batch(
    text: str,
    *,
    node_type: str = "h100",
    expected_gpu_count: int | None = None,
    expected_min_bw: float = 360.0,
) -> dict[str, Any]:
    """Parse multi-host diagnostic output.

    *text* contains output from multiple nodes separated by node headers
    (``--- hostname ---``, ``### hostname``, or bare FQDNs) and section
    markers (``=IB=``, ``=ECC=``, ``=RETIRED=``, ``=KERNEL=``, ``=NVLINK=``,
    ``=NCCL=``).

    Each section is routed to the appropriate parser.  Results are returned
    as a per-node summary ranked by severity (critical first).
    """
    if not text or not text.strip():
        return _empty_result()

    raw_nodes = split_nodes(text)
    if not raw_nodes:
        return _empty_result()

    topo = ibstat.NODE_TOPOLOGIES.get(node_type.lower(), ibstat.NODE_TOPOLOGIES["h100"])

    nodes: list[dict[str, Any]] = []
    for node_name, node_text in raw_nodes.items():
        sections = _split_sections(node_text)
        checks = _run_checks(sections, topo, expected_gpu_count, expected_min_bw)
        overall = _max_severity(c["severity"] for c in checks.values())
        nodes.append(
            {
                "node": node_name,
                "overall_severity": overall,
                "checks": checks,
            }
        )

    nodes.sort(key=lambda n: _SEVERITY_ORDER.get(n["overall_severity"], 0), reverse=True)

    counts = {"ok": 0, "warning": 0, "critical": 0}
    for n in nodes:
        sev = n["overall_severity"]
        if sev in counts:
            counts[sev] += 1

    worst = [n["node"] for n in nodes if n["overall_severity"] == "critical"]

    overall_sev = "ok"
    if counts["critical"]:
        overall_sev = "critical"
    elif counts["warning"]:
        overall_sev = "warning"

    return {
        "nodes": nodes,
        "summary": {
            "total_nodes": len(nodes),
            "critical": counts["critical"],
            "warning": counts["warning"],
            "ok": counts["ok"],
        },
        "worst_nodes": worst,
        "severity": overall_sev,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def split_nodes(text: str) -> dict[str, str]:
    """Split text into per-node chunks keyed by hostname.

    Public helper — also used by ``diagnose_nccl_failure`` in server.py.
    """
    nodes: dict[str, str] = {}
    current_node: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _SECTION_RE.match(line):
            if current_node is not None:
                current_lines.append(raw_line)
            continue
        m = NODE_HEADER_RE.match(line)
        if m:
            if current_node and current_lines:
                nodes[current_node] = "\n".join(current_lines)
            current_node = next(g for g in m.groups() if g)
            current_lines = []
            continue
        if current_node is not None:
            current_lines.append(raw_line)

    if current_node and current_lines:
        nodes[current_node] = "\n".join(current_lines)

    return nodes


def _split_sections(text: str) -> dict[str, str]:
    """Split a single node's text by ``=SECTION=`` markers."""
    sections: dict[str, str] = {}
    current_tag: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        m = _SECTION_RE.match(raw_line.strip())
        if m:
            tag = m.group(1).lower()
            if tag in _SECTION_TAGS:
                if current_tag is not None:
                    sections[current_tag] = "\n".join(current_lines)
                current_tag = tag
                current_lines = []
                continue
        if current_tag is not None:
            current_lines.append(raw_line)

    if current_tag is not None:
        sections[current_tag] = "\n".join(current_lines)

    return sections


def _run_checks(
    sections: dict[str, str],
    topo: dict[str, frozenset[str]],
    expected_gpu_count: int | None,
    expected_min_bw: float,
) -> dict[str, dict[str, Any]]:
    """Route each section to its parser and return compact check results.

    Each parser is wrapped in try/except so one malformed section cannot
    crash the entire batch — remaining sections and nodes still produce
    results.
    """
    checks: dict[str, dict[str, Any]] = {}

    if "ib" in sections:
        try:
            raw = ibstat.parse_ibdev2netdev(
                sections["ib"],
                expected_ib_devices=topo["ib"],
                expected_eth_devices=topo["eth"],
            )
            checks["ib"] = {
                "severity": raw["severity"],
                "all_ib_up": raw["all_ib_up"],
                "ports_down": raw["ports_down"],
                "ports_up_count": raw["ports_up_count"],
            }
        except Exception as exc:
            log.warning("IB parser failed: %s", exc)
            checks["ib"] = {"severity": "unknown", "error": str(exc)}

    if "ecc" in sections:
        try:
            raw = ecc.parse_ecc_csv(sections["ecc"])
            checks["ecc"] = {
                "severity": raw["severity"],
                "total_uncorrectable": raw["summary"]["total_uncorrectable"],
                "total_correctable": raw["summary"]["total_correctable"],
                "any_volatile_uncorrectable": raw["summary"]["any_volatile_uncorrectable"],
            }
        except Exception as exc:
            log.warning("ECC parser failed: %s", exc)
            checks["ecc"] = {"severity": "unknown", "error": str(exc)}

    if "retired" in sections:
        try:
            raw = retired_pages.parse_retired_pages(
                sections["retired"],
                expected_gpu_count=expected_gpu_count,
            )
            checks["retired_pages"] = {
                "severity": raw["severity"],
                "total_retired": raw["summary"]["total_retired"],
                "is_normal_baseline": raw["summary"]["is_normal_baseline"],
                "gpu_count": raw["summary"]["gpu_count"],
            }
        except Exception as exc:
            log.warning("Retired pages parser failed: %s", exc)
            checks["retired_pages"] = {"severity": "unknown", "error": str(exc)}

    if "kernel" in sections:
        try:
            raw = kernel_logs.parse_kernel_xid_logs(sections["kernel"])
            checks["kernel_logs"] = {
                "severity": raw["severity"],
                "total_xid": raw["summary"]["total_xid"],
                "total_sxid": raw["summary"]["total_sxid"],
                "unique_xid_codes": raw["summary"]["unique_xid_codes"],
            }
        except Exception as exc:
            log.warning("Kernel log parser failed: %s", exc)
            checks["kernel_logs"] = {"severity": "unknown", "error": str(exc)}

    if "nvlink" in sections:
        try:
            raw = nvlink.parse_nvlink_status(sections["nvlink"])
            checks["nvlink"] = {
                "severity": raw["severity"],
                "total_gpus": raw["total_gpus"],
                "any_issue": any(g.get("has_issue") for g in raw.get("gpus", [])),
            }
        except Exception as exc:
            log.warning("NVLink parser failed: %s", exc)
            checks["nvlink"] = {"severity": "unknown", "error": str(exc)}

    if "nccl" in sections:
        try:
            raw = nccl.parse_nccl_results(
                sections["nccl"],
                expected_min_bw=expected_min_bw,
            )
            checks["nccl"] = {
                "severity": raw["severity"],
                "success": raw["success"],
                "avg_busbw": raw["avg_busbw"],
                "failures": raw["failures"],
            }
        except Exception as exc:
            log.warning("NCCL parser failed: %s", exc)
            checks["nccl"] = {"severity": "unknown", "error": str(exc)}

    return checks


def _max_severity(severities: Iterable[str]) -> str:
    """Return the highest severity from an iterable of severity strings."""
    best = "ok"
    for s in severities:
        if _SEVERITY_ORDER.get(s, 0) > _SEVERITY_ORDER.get(best, 0):
            best = s
    return best


def _empty_result() -> dict[str, Any]:
    return {
        "nodes": [],
        "summary": {
            "total_nodes": 0,
            "critical": 0,
            "warning": 0,
            "ok": 0,
        },
        "worst_nodes": [],
        "severity": "ok",
    }
