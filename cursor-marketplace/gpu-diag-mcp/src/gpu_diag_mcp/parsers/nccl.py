"""Parse NCCL test (all_reduce_perf) output and detect failure patterns."""

from __future__ import annotations

import re
from typing import Any

_BW_HEADER_RE = re.compile(
    r"#\s+size\s+count\s+type\s+redop\s+root\s+time\s+algbw\s+busbw",
    re.IGNORECASE,
)
_AVG_BW_RE = re.compile(r"#\s*Avg\s+bus\s+bandwidth\s*:\s*([\d.]+)", re.IGNORECASE)
_DATA_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(-?\d+)"
    r"\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+(\d+)"
    r"\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+(\d+)"
)
_INIT_COMPLETE_RE = re.compile(r"NCCL INFO.*Init COMPLETE", re.IGNORECASE)
_HCA_RE = re.compile(r"NET/IB\s*:\s*Using\s*\[(\d+)\]", re.IGNORECASE)
_WAITING_RE = re.compile(r"Waiting for master\s+(\S+)", re.IGNORECASE)
_OOM_RE = re.compile(r"out of memory", re.IGNORECASE)
_OMP_WARN_RE = re.compile(r"OMP_NUM_THREADS", re.IGNORECASE)


def parse_nccl_results(
    text: str,
    expected_gpus: int = 8,
    expected_min_bw: float = 360.0,
) -> dict[str, Any]:
    """Parse all_reduce_perf output.

    *expected_gpus* defaults to 8 (H100).  Pass 4 for GB200 nodes.

    Returns performance data from successful runs, or detected failure
    patterns for failed runs.
    """
    if not text or not text.strip():
        return _empty_result(expected_gpus=expected_gpus, expected_min_bw=expected_min_bw)

    lines = text.splitlines()

    avg_busbw: float | None = None
    data_rows: list[dict[str, Any]] = []
    init_complete = False
    has_header = False
    failures: list[str] = []
    has_omp_warning = False
    waiting_for: list[str] = []
    hca_counts: list[int] = []
    wrong_count = 0

    for line in lines:
        if _BW_HEADER_RE.search(line):
            has_header = True
            continue

        m = _AVG_BW_RE.search(line)
        if m:
            avg_busbw = float(m.group(1))
            continue

        m = _DATA_RE.match(line)
        if m:
            errors = int(m.group(9)) + int(m.group(13))
            wrong_count += errors
            data_rows.append(
                {
                    "size": int(m.group(1)),
                    "count": int(m.group(2)),
                    "type": m.group(3),
                    "redop": m.group(4),
                    "out_of_place_busbw": float(m.group(8)),
                    "in_place_busbw": float(m.group(12)),
                    "errors": errors,
                }
            )
            continue

        if _INIT_COMPLETE_RE.search(line):
            init_complete = True
            continue

        m = _HCA_RE.search(line)
        if m:
            hca_counts.append(int(m.group(1)))
            continue

        m = _WAITING_RE.search(line)
        if m:
            waiting_for.append(m.group(1))
            continue

        if _OOM_RE.search(line):
            failures.append("out_of_memory")
            continue

        if _OMP_WARN_RE.search(line):
            has_omp_warning = True

    # Detect failure patterns
    if has_omp_warning and not init_complete and not has_header:
        failures.append("bootstrap_hang")

    if waiting_for:
        failures.append("peer_waiting")

    if hca_counts and any(c < expected_gpus for c in hca_counts):
        failures.append("missing_hcas")

    # Bandwidth assessment
    bw_ok = avg_busbw is not None and avg_busbw >= expected_min_bw
    bw_low = avg_busbw is not None and avg_busbw < expected_min_bw
    if bw_low:
        failures.append("low_bandwidth")

    if wrong_count > 0:
        failures.append("data_corruption")

    severity = "ok"
    if failures:
        severity = (
            "critical"
            if any(f in ("out_of_memory", "bootstrap_hang", "data_corruption") for f in failures)
            else "warning"
        )
    elif bw_low:
        severity = "warning"

    return {
        "success": not failures and bw_ok,
        "avg_busbw": avg_busbw,
        "expected_min_bw": expected_min_bw,
        "expected_gpus": expected_gpus,
        "data_rows": data_rows,
        "init_complete": init_complete,
        "failures": failures,
        "waiting_for": waiting_for,
        "hca_counts": hca_counts,
        "wrong_count": wrong_count,
        "severity": severity,
    }


def _empty_result(
    expected_gpus: int = 8,
    expected_min_bw: float = 360.0,
) -> dict[str, Any]:
    return {
        "success": False,
        "avg_busbw": None,
        "expected_min_bw": expected_min_bw,
        "expected_gpus": expected_gpus,
        "data_rows": [],
        "init_complete": False,
        "failures": ["no_output"],
        "waiting_for": [],
        "hca_counts": [],
        "wrong_count": 0,
        "severity": "critical",
    }
