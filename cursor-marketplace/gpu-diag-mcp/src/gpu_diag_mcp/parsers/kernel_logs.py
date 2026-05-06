"""Parse XID, SXid, FBHUB, and assertion failures from kernel logs."""

from __future__ import annotations

import re
from typing import Any

# Apr 02 01:34:49 hostname kernel: NVRM: Xid (PCI:0000:04:00): 94, pid=207955, ...
_XID_RE = re.compile(
    r"(?P<timestamp>\w{3}\s+\d{2}\s+[\d:]+)\s+\S+\s+kernel:\s+NVRM:\s+"
    r"Xid\s+\(PCI:(?P<pci>[^)]+)\):\s+(?P<xid>\d+)"
    r"(?:,\s+pid=(?P<pid>\d+))?"
    r"(?:,\s+name=(?P<name>[^\s,]+))?"
    r"(?:,\s+channel\s+(?P<channel>\S+))?"
)

# nvidia-nvswitch2: SXid (PCI:0000:0b:00.0): 12028, ...
_SXID_RE = re.compile(
    r"(?P<timestamp>\w{3}\s+\d{2}\s+[\d:]+)\s+\S+\s+kernel:\s+"
    r"nvidia-nvswitch\d+:\s+SXid\s+\(PCI:(?P<pci>[^)]+)\):\s+(?P<sxid>\d+)"
    r"(?:,\s+(?P<tail>.+))?"
)

# NVRM: GPU0 gpuClearFbhubPoisonIntrForBug...: FBHUB Interrupt detected.
_FBHUB_RE = re.compile(
    r"(?P<timestamp>\w{3}\s+\d{2}\s+[\d:]+)\s+\S+\s+kernel:\s+NVRM:\s+"
    r"GPU(?P<gpu>\d+)\s+\S*FBHUB\S*:\s+(?P<message>.+)",
    re.IGNORECASE,
)

# NVRM: GPU2 nvAssertFailedNoLog: Assertion failed: ...
_ASSERT_RE = re.compile(
    r"(?P<timestamp>\w{3}\s+\d{2}\s+[\d:]+)\s+\S+\s+kernel:\s+NVRM:\s+"
    r"GPU(?P<gpu>\d+)\s+\S*Assert\S*:\s+(?P<assertion>.+)",
    re.IGNORECASE,
)

# PCI bus domain:bus:device -> approximate GPU index mapping.
# H100 8-GPU nodes typically use buses 04,0d,17,27,3a,43,56,65 (slot order).
_PCI_BUS_RE = re.compile(r"(\w{4}):(\w{2}):(\w{2})")


def _gpu_index_from_pci(pci: str) -> int | None:
    """Best-effort GPU index from PCI bus ID (bus number order)."""
    m = _PCI_BUS_RE.search(pci)
    if not m:
        return None
    return int(m.group(2), 16)


def _parse_timestamp_seconds(ts: str) -> float:
    """Convert 'Apr 02 01:34:49' to seconds-since-midnight for proximity checks."""
    parts = ts.strip().split()
    if len(parts) < 3:
        return 0.0
    time_parts = parts[2].split(":")
    if len(time_parts) != 3:
        return 0.0
    return int(time_parts[0]) * 3600 + int(time_parts[1]) * 60 + int(time_parts[2])


def parse_kernel_xid_logs(log_text: str) -> dict[str, Any]:
    """Parse kernel log text for XID, SXid, FBHUB interrupts, and assertion failures.

    Returns a dict with keys: xid_events, sxid_events, fbhub_events,
    assert_failures, summary.
    """
    if not log_text or not log_text.strip():
        return _empty_result()

    xid_events: list[dict[str, Any]] = []
    sxid_events: list[dict[str, Any]] = []
    fbhub_events: list[dict[str, Any]] = []
    assert_failures: list[dict[str, Any]] = []

    for line in log_text.splitlines():
        if _try_xid(line, xid_events):
            continue
        if _try_sxid(line, sxid_events):
            continue
        if _try_fbhub(line, fbhub_events):
            continue
        _try_assert(line, assert_failures)

    unique_xid = sorted({e["xid_code"] for e in xid_events})
    unique_sxid = sorted({e["sxid_code"] for e in sxid_events})

    is_boot_time_fbhub = _check_boot_fbhub(fbhub_events)

    severity = "ok"
    if xid_events or (sxid_events and any(e.get("severity") != "0" for e in sxid_events)):
        severity = "critical"
    elif sxid_events or assert_failures or (fbhub_events and not is_boot_time_fbhub):
        severity = "warning"

    return {
        "xid_events": xid_events,
        "sxid_events": sxid_events,
        "fbhub_events": fbhub_events,
        "assert_failures": assert_failures,
        "summary": {
            "total_xid": len(xid_events),
            "total_sxid": len(sxid_events),
            "total_fbhub": len(fbhub_events),
            "unique_xid_codes": unique_xid,
            "unique_sxid_codes": unique_sxid,
            "is_boot_time_fbhub": is_boot_time_fbhub,
        },
        "severity": severity,
    }


def _try_xid(line: str, out: list[dict[str, Any]]) -> bool:
    m = _XID_RE.search(line)
    if not m:
        return False
    pci = m.group("pci")
    out.append(
        {
            "timestamp": m.group("timestamp"),
            "pci_bus": pci,
            "xid_code": int(m.group("xid")),
            "pid": int(m.group("pid")) if m.group("pid") else None,
            "process_name": m.group("name"),
            "channel": m.group("channel"),
            "gpu_index": _gpu_index_from_pci(pci),
        }
    )
    return True


def _try_sxid(line: str, out: list[dict[str, Any]]) -> bool:
    m = _SXID_RE.search(line)
    if not m:
        return False
    tail = (m.group("tail") or "").strip()

    sev = None
    sev_m = re.search(r"Severity\s+(\d+)", tail)
    if sev_m:
        sev = sev_m.group(1)

    engine = None
    eng_m = re.search(r"Engine instance\s+(\d+)", tail)
    if eng_m:
        engine = int(eng_m.group(1))

    out.append(
        {
            "timestamp": m.group("timestamp"),
            "pci_bus": m.group("pci"),
            "sxid_code": int(m.group("sxid")),
            "severity": sev,
            "engine_instance": engine,
            "details": tail if tail else None,
        }
    )
    return True


def _try_fbhub(line: str, out: list[dict[str, Any]]) -> bool:
    m = _FBHUB_RE.search(line)
    if not m:
        return False
    out.append(
        {
            "timestamp": m.group("timestamp"),
            "gpu_index": int(m.group("gpu")),
            "message": m.group("message").strip(),
        }
    )
    return True


def _try_assert(line: str, out: list[dict[str, Any]]) -> bool:
    m = _ASSERT_RE.search(line)
    if not m:
        return False
    out.append(
        {
            "timestamp": m.group("timestamp"),
            "gpu_index": int(m.group("gpu")),
            "assertion": m.group("assertion").strip(),
        }
    )
    return True


def _check_boot_fbhub(events: list[dict[str, Any]]) -> bool:
    """Boot-time FBHUB: exactly 8 events all within a few seconds of each other."""
    if len(events) != 8:
        return False
    times = [_parse_timestamp_seconds(e["timestamp"]) for e in events]
    return (max(times) - min(times)) <= 10.0


def _empty_result() -> dict[str, Any]:
    return {
        "xid_events": [],
        "sxid_events": [],
        "fbhub_events": [],
        "assert_failures": [],
        "summary": {
            "total_xid": 0,
            "total_sxid": 0,
            "total_fbhub": 0,
            "unique_xid_codes": [],
            "unique_sxid_codes": [],
            "is_boot_time_fbhub": False,
        },
        "severity": "ok",
    }
