"""Parse retired pages and remapped rows from nvidia-smi."""

from __future__ import annotations

import re
from typing import Any

# Normal retired pages per GPU (1 SBE + 1 DBE) across all GPU types.
NORMAL_PER_GPU = 2

# H100 baseline: 8 GPUs x 2 pages = 16 total
H100_NORMAL_BASELINE = 16
H100_NORMAL_PER_GPU = NORMAL_PER_GPU  # backwards-compatible alias


def parse_retired_pages(
    text: str,
    *,
    expected_gpu_count: int | None = None,
) -> dict[str, Any]:
    """Parse ``nvidia-smi --query-retired-pages=...`` CSV output.

    Expected columns: gpu_uuid, retired_pages.address, retired_pages.cause

    When *expected_gpu_count* is given, the normal baseline is computed as
    ``expected_gpu_count * 2`` (1 SBE + 1 DBE per GPU).  When omitted the
    baseline is inferred from the actual number of GPUs present in the data.
    """
    if not text or not text.strip():
        return _empty_retired()

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return _empty_retired()

    per_gpu: dict[str, dict[str, int]] = {}

    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        uuid = parts[0]
        cause = parts[2].strip().lower()
        if uuid not in per_gpu:
            per_gpu[uuid] = {"single_bit_ecc": 0, "double_bit_ecc": 0, "total": 0}

        if "single" in cause:
            per_gpu[uuid]["single_bit_ecc"] += 1
        elif "double" in cause:
            per_gpu[uuid]["double_bit_ecc"] += 1
        per_gpu[uuid]["total"] += 1

    gpus = [{"gpu_uuid": uuid, **counts} for uuid, counts in sorted(per_gpu.items())]

    total = sum(g["total"] for g in gpus)
    total_sbe = sum(g["single_bit_ecc"] for g in gpus)
    total_dbe = sum(g["double_bit_ecc"] for g in gpus)

    gpu_count = len(gpus)
    effective_gpu_count = expected_gpu_count if expected_gpu_count is not None else gpu_count
    normal_baseline = effective_gpu_count * NORMAL_PER_GPU

    is_normal_baseline = (
        total == normal_baseline
        and all(g["total"] == NORMAL_PER_GPU for g in gpus)
        and all(g["single_bit_ecc"] == 1 and g["double_bit_ecc"] == 1 for g in gpus)
    )

    severity = "ok"
    if not is_normal_baseline:
        if total_dbe > gpu_count:
            severity = "critical"
        elif total > normal_baseline:
            severity = "warning"

    return {
        "gpus": gpus,
        "summary": {
            "total_retired": total,
            "total_single_bit": total_sbe,
            "total_double_bit": total_dbe,
            "is_normal_baseline": is_normal_baseline,
            "normal_baseline": normal_baseline,
            "gpu_count": gpu_count,
        },
        "severity": severity,
    }


def parse_remapped_rows(text: str) -> dict[str, Any]:
    """Parse ``nvidia-smi --query-remapped-rows=...`` CSV output.

    Expected columns:
      gpu_uuid, remapped_rows.correctable, remapped_rows.uncorrectable,
      remapped_rows.pending, remapped_rows.failure
    """
    if not text or not text.strip():
        return _empty_remapped()

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return _empty_remapped()

    gpus: list[dict[str, Any]] = []
    any_pending = False
    any_failure = False
    total_corr = 0
    total_uncorr = 0

    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        uuid = parts[0]
        corr = _safe_int(parts[1])
        uncorr = _safe_int(parts[2])
        pending = _parse_bool(parts[3])
        failure = _parse_bool(parts[4])

        gpus.append(
            {
                "gpu_uuid": uuid,
                "correctable": corr,
                "uncorrectable": uncorr,
                "pending": pending,
                "failure": failure,
            }
        )
        total_corr += corr
        total_uncorr += uncorr
        if pending:
            any_pending = True
        if failure:
            any_failure = True

    severity = "ok"
    if any_failure:
        severity = "critical"
    elif any_pending or total_uncorr > 0:
        severity = "warning"

    return {
        "gpus": gpus,
        "summary": {
            "total_correctable": total_corr,
            "total_uncorrectable": total_uncorr,
            "any_pending": any_pending,
            "any_failure": any_failure,
            "gpu_count": len(gpus),
        },
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRUTHY = re.compile(r"^(yes|true|1)$", re.IGNORECASE)


def _parse_bool(s: str) -> bool:
    return bool(_TRUTHY.match(s.strip()))


def _safe_int(s: str) -> int:
    s = s.strip()
    if s.lower() in ("n/a", "[n/a]", "-", ""):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _empty_retired() -> dict[str, Any]:
    return {
        "gpus": [],
        "summary": {
            "total_retired": 0,
            "total_single_bit": 0,
            "total_double_bit": 0,
            "is_normal_baseline": False,
            "gpu_count": 0,
        },
        "severity": "ok",
    }


def _empty_remapped() -> dict[str, Any]:
    return {
        "gpus": [],
        "summary": {
            "total_correctable": 0,
            "total_uncorrectable": 0,
            "any_pending": False,
            "any_failure": False,
            "gpu_count": 0,
        },
        "severity": "ok",
    }
