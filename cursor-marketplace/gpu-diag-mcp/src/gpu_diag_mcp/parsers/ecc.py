"""Parse ECC error output from nvidia-smi."""

from __future__ import annotations

import re
from typing import Any

_HIGH_CORRECTABLE_THRESHOLD = 1000


def parse_ecc_csv(text: str) -> dict[str, Any]:
    """Parse ``nvidia-smi --query-gpu=index,ecc.errors.* --format=csv`` output.

    Expected CSV columns:
      index, ecc.errors.corrected.volatile.total,
      ecc.errors.uncorrected.volatile.total,
      ecc.errors.corrected.aggregate.total,
      ecc.errors.uncorrected.aggregate.total
    """
    if not text or not text.strip():
        return _empty_result()

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return _empty_result()

    gpus: list[dict[str, Any]] = []
    total_corr = 0
    total_uncorr = 0
    any_vol_uncorr = False
    any_agg_uncorr = False
    high_agg_corr = False

    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        idx = _safe_int(parts[0])
        vol_corr = _safe_int(parts[1])
        vol_uncorr = _safe_int(parts[2])
        agg_corr = _safe_int(parts[3])
        agg_uncorr = _safe_int(parts[4])

        gpus.append(
            {
                "index": idx,
                "volatile_correctable": vol_corr,
                "volatile_uncorrectable": vol_uncorr,
                "aggregate_correctable": agg_corr,
                "aggregate_uncorrectable": agg_uncorr,
            }
        )

        total_corr += vol_corr + agg_corr
        total_uncorr += vol_uncorr + agg_uncorr
        if vol_uncorr > 0:
            any_vol_uncorr = True
        if agg_uncorr > 0:
            any_agg_uncorr = True
        if agg_corr > _HIGH_CORRECTABLE_THRESHOLD:
            high_agg_corr = True

    severity = _compute_severity(any_vol_uncorr, any_agg_uncorr, high_agg_corr)

    return {
        "gpus": gpus,
        "summary": {
            "any_volatile_uncorrectable": any_vol_uncorr,
            "any_aggregate_uncorrectable": any_agg_uncorr,
            "high_aggregate_correctable": high_agg_corr,
            "total_correctable": total_corr,
            "total_uncorrectable": total_uncorr,
        },
        "severity": severity,
    }


def parse_ecc_full(text: str) -> dict[str, Any]:
    """Parse ``nvidia-smi -q -d ECC`` verbose output.

    Extracts per-GPU SRAM/DRAM breakdown, pending retirement, ECC mode, and
    threshold-exceeded flags.
    """
    if not text or not text.strip():
        return _empty_result()

    gpus: list[dict[str, Any]] = []
    current_gpu: dict[str, Any] | None = None
    section_stack: list[str] = []

    total_corr = 0
    total_uncorr = 0
    any_vol_uncorr = False
    any_agg_uncorr = False
    high_agg_corr = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # GPU header: "GPU 00000000:04:00.0"
        gpu_m = re.match(r"GPU\s+[\da-fA-F:.\s]+", stripped)
        if gpu_m:
            if current_gpu is not None:
                gpus.append(current_gpu)
            current_gpu = _new_gpu_record(len(gpus))
            section_stack = []
            continue

        if current_gpu is None:
            continue

        # Section headers end with a colon and have no value after it
        if stripped.endswith(":") and ":" not in stripped[:-1].split("(")[-1]:
            section_stack = _update_section_stack(section_stack, line, stripped)
            continue

        # Key : Value pairs
        kv = re.match(r"(.+?)\s*:\s*(.+)", stripped)
        if not kv:
            continue
        key = kv.group(1).strip()
        val = kv.group(2).strip()

        section_path = " > ".join(section_stack).lower()
        _populate_gpu_record(current_gpu, section_path, key, val)

    if current_gpu is not None:
        gpus.append(current_gpu)

    # Compute summary from parsed GPU records
    for gpu in gpus:
        vol = gpu.get("volatile", {})
        agg = gpu.get("aggregate", {})
        v_corr = _sum_sram_dram(vol, "correctable")
        v_uncorr = _sum_sram_dram(vol, "uncorrectable")
        a_corr = _sum_sram_dram(agg, "correctable")
        a_uncorr = _sum_sram_dram(agg, "uncorrectable")
        total_corr += v_corr + a_corr
        total_uncorr += v_uncorr + a_uncorr
        if v_uncorr > 0:
            any_vol_uncorr = True
        if a_uncorr > 0:
            any_agg_uncorr = True
        if a_corr > _HIGH_CORRECTABLE_THRESHOLD:
            high_agg_corr = True

    severity = _compute_severity(any_vol_uncorr, any_agg_uncorr, high_agg_corr)

    return {
        "gpus": gpus,
        "summary": {
            "any_volatile_uncorrectable": any_vol_uncorr,
            "any_aggregate_uncorrectable": any_agg_uncorr,
            "high_aggregate_correctable": high_agg_corr,
            "total_correctable": total_corr,
            "total_uncorrectable": total_uncorr,
        },
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(s: str) -> int:
    """Parse an integer, treating N/A or non-numeric as 0."""
    s = s.strip()
    if s.lower() in ("n/a", "[n/a]", "-", ""):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _compute_severity(any_vol_uncorr: bool, any_agg_uncorr: bool, high_agg_corr: bool) -> str:
    if any_vol_uncorr or any_agg_uncorr:
        return "critical"
    if high_agg_corr:
        return "warning"
    return "ok"


def _new_gpu_record(index: int) -> dict[str, Any]:
    return {
        "index": index,
        "ecc_mode_current": None,
        "ecc_mode_pending": None,
        "volatile": {
            "sram_correctable": 0,
            "sram_uncorrectable": 0,
            "dram_correctable": 0,
            "dram_uncorrectable": 0,
        },
        "aggregate": {
            "sram_correctable": 0,
            "sram_uncorrectable": 0,
            "dram_correctable": 0,
            "dram_uncorrectable": 0,
        },
        "retired_pages_sbe": None,
        "retired_pages_dbe": None,
        "pending_retirement": None,
    }


def _update_section_stack(stack: list[str], raw_line: str, stripped: str) -> list[str]:
    """Maintain a section nesting stack based on indentation."""
    indent = len(raw_line) - len(raw_line.lstrip())
    depth = indent // 4
    section_name = stripped.rstrip(":")
    if depth < len(stack):
        stack = stack[:depth]
    stack.append(section_name)
    return stack


def _populate_gpu_record(gpu: dict[str, Any], section: str, key: str, val: str) -> None:
    """Place a key/value into the correct spot in the GPU record."""
    kl = key.lower()
    vl = val.lower()

    if "ecc mode" in section:
        if "current" in kl:
            gpu["ecc_mode_current"] = val
        elif "pending" in kl:
            gpu["ecc_mode_pending"] = val
        return

    if "retired pages" in section or "retirement" in section:
        if "single bit" in kl or "sbe" in kl:
            gpu["retired_pages_sbe"] = _safe_int(val)
        elif "double bit" in kl or "dbe" in kl:
            gpu["retired_pages_dbe"] = _safe_int(val)
        elif "pending" in kl:
            gpu["pending_retirement"] = vl not in ("no", "false", "0")
        return

    bucket: dict[str, int] | None = None
    if "volatile" in section:
        bucket = gpu["volatile"]
    elif "aggregate" in section:
        bucket = gpu["aggregate"]
    if bucket is None:
        return

    mem = "sram" if "sram" in section else "dram" if "dram" in section else None
    if mem is None:
        return

    err_type = None
    if "correctable" in kl or "corrected" in kl:
        err_type = "correctable"
    elif "uncorrectable" in kl or "uncorrected" in kl:
        err_type = "uncorrectable"
    if err_type is None and "total" in kl:
        # Guess from section context
        if "uncorrectable" in section or "uncorrected" in section:
            err_type = "uncorrectable"
        else:
            err_type = "correctable"
    if err_type:
        bucket[f"{mem}_{err_type}"] = _safe_int(val)


def _sum_sram_dram(bucket: dict[str, int], err_type: str) -> int:
    return bucket.get(f"sram_{err_type}", 0) + bucket.get(f"dram_{err_type}", 0)


def _empty_result() -> dict[str, Any]:
    return {
        "gpus": [],
        "summary": {
            "any_volatile_uncorrectable": False,
            "any_aggregate_uncorrectable": False,
            "high_aggregate_correctable": False,
            "total_correctable": 0,
            "total_uncorrectable": 0,
        },
        "severity": "ok",
    }
