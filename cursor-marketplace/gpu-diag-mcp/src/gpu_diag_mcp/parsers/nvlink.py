"""Parse NVLink status and error counters from nvidia-smi."""

from __future__ import annotations

import re
from typing import Any

_GPU_HEADER_RE = re.compile(r"GPU\s+(\d+):\s+(.+?)(?:\s*\(UUID:\s*(\S+)\))?\s*$")
_LINK_STATUS_RE = re.compile(r"Link\s+(\d+):\s+([\d.]+)\s+GB/s")
_LINK_INACTIVE_RE = re.compile(r"Link\s+(\d+):\s+inactive", re.IGNORECASE)

_LINK_ERROR_RE = re.compile(
    r"Link\s+(\d+):\s+(Replay Errors|Recovery Errors|CRC Errors|CRC Flit Errors)\s*:\s*(\d+)",
    re.IGNORECASE,
)

EXPECTED_LINKS_PER_GPU = 18
EXPECTED_LINK_SPEED = 26.562


def parse_nvlink_status(text: str) -> dict[str, Any]:
    """Parse ``nvidia-smi nvlink --status`` output.

    Expects 18 links per H100 GPU at 26.562 GB/s. Flags inactive or
    degraded-speed links.
    """
    if not text or not text.strip():
        return _empty_status()

    gpus: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        gm = _GPU_HEADER_RE.match(line.strip())
        if gm:
            if current is not None:
                _finalize_status_gpu(current)
                gpus.append(current)
            current = {
                "index": int(gm.group(1)),
                "name": gm.group(2).strip(),
                "uuid": gm.group(3),
                "links": [],
                "inactive_links": [],
                "degraded_links": [],
            }
            continue

        if current is None:
            continue

        lm = _LINK_STATUS_RE.search(line)
        if lm:
            link_id = int(lm.group(1))
            speed = float(lm.group(2))
            current["links"].append({"link": link_id, "speed_gbps": speed})
            if abs(speed - EXPECTED_LINK_SPEED) > 0.01:
                current["degraded_links"].append(link_id)
            continue

        im = _LINK_INACTIVE_RE.search(line)
        if im:
            current["inactive_links"].append(int(im.group(1)))

    if current is not None:
        _finalize_status_gpu(current)
        gpus.append(current)

    any_issue = any(g.get("has_issue") for g in gpus)
    severity = "ok"
    if any_issue:
        severity = "critical" if any(g.get("inactive_links") for g in gpus) else "warning"

    return {
        "gpus": gpus,
        "total_gpus": len(gpus),
        "severity": severity,
    }


def parse_nvlink_errors(text: str) -> dict[str, Any]:
    """Parse ``nvidia-smi nvlink -e`` error counter output.

    Sums replay, recovery, and CRC errors per GPU. Flags any non-zero
    counters.
    """
    if not text or not text.strip():
        return _empty_errors()

    gpus: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        gm = _GPU_HEADER_RE.match(line.strip())
        if gm:
            if current is not None:
                _finalize_error_gpu(current)
                gpus.append(current)
            current = {
                "index": int(gm.group(1)),
                "name": gm.group(2).strip(),
                "link_errors": {},
                "total_errors": 0,
                "has_errors": False,
            }
            continue

        if current is None:
            continue

        em = _LINK_ERROR_RE.search(line)
        if em:
            link_id = int(em.group(1))
            err_type = em.group(2).strip().lower().replace(" ", "_")
            count = int(em.group(3))
            if link_id not in current["link_errors"]:
                current["link_errors"][link_id] = {}
            current["link_errors"][link_id][err_type] = count

    if current is not None:
        _finalize_error_gpu(current)
        gpus.append(current)

    any_errors = any(g["has_errors"] for g in gpus)
    total = sum(g["total_errors"] for g in gpus)

    severity = "ok"
    if any_errors:
        severity = "critical" if total > 100 else "warning"

    return {
        "gpus": gpus,
        "total_gpus": len(gpus),
        "total_errors": total,
        "any_errors": any_errors,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finalize_status_gpu(gpu: dict[str, Any]) -> None:
    active = len(gpu["links"])
    gpu["active_link_count"] = active
    gpu["expected_links"] = EXPECTED_LINKS_PER_GPU
    gpu["has_issue"] = (
        active < EXPECTED_LINKS_PER_GPU
        or bool(gpu["inactive_links"])
        or bool(gpu["degraded_links"])
    )


def _finalize_error_gpu(gpu: dict[str, Any]) -> None:
    total = 0
    for link_errs in gpu["link_errors"].values():
        total += sum(link_errs.values())
    gpu["total_errors"] = total
    gpu["has_errors"] = total > 0


def _empty_status() -> dict[str, Any]:
    return {"gpus": [], "total_gpus": 0, "severity": "ok"}


def _empty_errors() -> dict[str, Any]:
    return {
        "gpus": [],
        "total_gpus": 0,
        "total_errors": 0,
        "any_errors": False,
        "severity": "ok",
    }
