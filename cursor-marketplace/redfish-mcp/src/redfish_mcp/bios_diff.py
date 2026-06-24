from __future__ import annotations

import re
from typing import Any

from .redfish import RedfishClient, RedfishEndpoint

# AI HINT: Different BIOS firmware versions use different attribute naming schemes.
# Example: BIOS 1.6 uses "SMTControl_0037", BIOS 3.7 uses "SMTControl"
# The numeric suffix (_XXXX) is a firmware quirk and should be stripped for semantic matching.

# Critical BIOS settings that should be highlighted in comparisons
CRITICAL_SETTINGS = {
    "SMTControl": {
        "name": "SMT Control (Hyper-Threading)",
        "category": "CPU",
        "impact": "Controls simultaneous multithreading; affects CPU core count and performance",
    },
    "IOMMU": {
        "name": "IOMMU (I/O Memory Management Unit)",
        "category": "Virtualization",
        "impact": "Required for device passthrough and VM isolation",
    },
    "SR_IOVSupport": {
        "name": "SR-IOV Support",
        "category": "Virtualization",
        "impact": "Enables Single Root I/O Virtualization for network/PCIe devices",
    },
    "SEVControl": {
        "name": "SEV (Secure Encrypted Virtualization)",
        "category": "Security",
        "impact": "AMD memory encryption for VM security",
    },
    "SVMMode": {
        "name": "SVM Mode (AMD-V)",
        "category": "Virtualization",
        "impact": "AMD hardware virtualization support",
    },
    "Above4GDecoding": {
        "name": "Above 4G Decoding",
        "category": "PCIe",
        "impact": "Allows PCIe devices to use memory above 4GB boundary",
    },
    "Re_SizeBARSupport": {
        "name": "Resizable BAR",
        "category": "PCIe",
        "impact": "Improves GPU performance by allowing CPU to access full GPU memory",
    },
    "ACSEnable": {
        "name": "ACS (Access Control Services)",
        "category": "PCIe",
        "impact": "PCIe isolation for passthrough; can affect peer-to-peer GPU communication",
    },
    "NUMANodesPerSocket": {
        "name": "NUMA Nodes Per Socket",
        "category": "Memory",
        "impact": "Memory locality configuration; critical for HPC performance",
    },
    "GlobalC_stateControl": {
        "name": "Global C-State Control",
        "category": "Power",
        "impact": "CPU power management; affects latency and power consumption",
    },
    "CorePerformanceBoost": {
        "name": "Core Performance Boost (CPB)",
        "category": "CPU",
        "impact": "AMD dynamic frequency scaling",
    },
}


def normalize_attribute_name(key: str) -> str:
    """
    Normalize BIOS attribute name by stripping numeric suffixes.

    AI HINT: Different BIOS versions use different naming conventions:
    - Older firmware: "SMTControl_0037", "IOMMU_0196"
    - Newer firmware: "SMTControl", "IOMMU"

    This function strips the _XXXX suffix to enable semantic matching.

    Examples:
        "SMTControl_0037" -> "SMTControl"
        "IOMMU_0196" -> "IOMMU"
        "Above4GDecoding_00B1" -> "Above4GDecoding"
        "SEVControl" -> "SEVControl" (unchanged)
    """
    # Strip underscore followed by 4 hex digits (common pattern)
    normalized = re.sub(r"_[0-9A-Fa-f]{4}$", "", key)
    return normalized


def get_bios_attributes(
    c: RedfishClient, ep: RedfishEndpoint
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (attributes_dict, bios_url, error_str)."""
    bios_url = f"{ep.system_url}/Bios"
    bios, err = c.get_json_maybe(bios_url)
    if err or not bios:
        return None, bios_url, err
    attrs = bios.get("Attributes")
    if not isinstance(attrs, dict):
        return None, bios_url, "No Attributes object in BIOS"
    return attrs, bios_url, None


def diff_attributes(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    keys_like: str | None = None,
) -> dict[str, Any]:
    """Return diff structure: {only_a, only_b, different, same, counts}."""
    all_keys = set(a.keys()) | set(b.keys())
    if keys_like:
        needle = keys_like.lower()
        all_keys = {k for k in all_keys if needle in k.lower()}

    only_a: list[dict[str, Any]] = []
    only_b: list[dict[str, Any]] = []
    different: list[dict[str, Any]] = []
    same: list[dict[str, Any]] = []

    for k in sorted(all_keys):
        if k not in b:
            only_a.append({"key": k, "value_a": a[k]})
        elif k not in a:
            only_b.append({"key": k, "value_b": b[k]})
        elif a[k] != b[k]:
            different.append({"key": k, "value_a": a[k], "value_b": b[k]})
        else:
            same.append({"key": k, "value": a[k]})

    return {
        "only_a": only_a,
        "only_b": only_b,
        "different": different,
        "same": same,
        "counts": {
            "only_a": len(only_a),
            "only_b": len(only_b),
            "different": len(different),
            "same": len(same),
            "total_keys": len(all_keys),
        },
    }


def diff_attributes_smart(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    keys_like: str | None = None,
) -> dict[str, Any]:
    """
    Smart BIOS attribute diff with semantic matching.

    AI HINT: This function handles BIOS firmware quirks where different versions
    use different attribute naming (e.g., "SMTControl_0037" vs "SMTControl").
    It normalizes names and matches attributes semantically.

    Returns:
        {
            "matched": [...],           # Semantically matched attributes
            "critical_differences": [...], # Important settings that differ
            "only_a": [...],            # Attributes unique to host A
            "only_b": [...],            # Attributes unique to host B
            "summary": {...},           # Human-readable summary
            "counts": {...}
        }
    """
    # Build normalized key mappings
    # Map: normalized_name -> {key_a: original_key_in_a, key_b: original_key_in_b}
    norm_map: dict[str, dict[str, str | None]] = {}

    for key_a in a:
        norm = normalize_attribute_name(key_a)
        if keys_like and keys_like.lower() not in norm.lower():
            continue
        if norm not in norm_map:
            norm_map[norm] = {"key_a": key_a, "key_b": None}
        else:
            norm_map[norm]["key_a"] = key_a

    for key_b in b:
        norm = normalize_attribute_name(key_b)
        if keys_like and keys_like.lower() not in norm.lower():
            continue
        if norm not in norm_map:
            norm_map[norm] = {"key_a": None, "key_b": key_b}
        else:
            norm_map[norm]["key_b"] = key_b

    # Categorize attributes
    matched: list[dict[str, Any]] = []
    only_a: list[dict[str, Any]] = []
    only_b: list[dict[str, Any]] = []
    critical_differences: list[dict[str, Any]] = []

    for norm_name, keys in sorted(norm_map.items()):
        key_a = keys["key_a"]
        key_b = keys["key_b"]

        if key_a and key_b:
            # Both hosts have this attribute (possibly with different names)
            val_a = a[key_a]
            val_b = b[key_b]

            entry = {
                "normalized_name": norm_name,
                "key_a": key_a,
                "key_b": key_b,
                "value_a": val_a,
                "value_b": val_b,
                "values_match": val_a == val_b,
            }

            # Check if this is a critical setting
            if norm_name in CRITICAL_SETTINGS:
                setting_info = CRITICAL_SETTINGS[norm_name]
                entry["is_critical"] = True
                entry["setting_name"] = setting_info["name"]
                entry["category"] = setting_info["category"]
                entry["impact"] = setting_info["impact"]

                if val_a != val_b:
                    critical_differences.append(entry)

            matched.append(entry)

        elif key_a:
            # Only in host A
            only_a.append(
                {
                    "normalized_name": norm_name,
                    "key": key_a,
                    "value": a[key_a],
                }
            )
        else:
            # Only in host B
            only_b.append(
                {
                    "normalized_name": norm_name,
                    "key": key_b,
                    "value": b[key_b],
                }
            )

    # Generate summary
    matched_same = [m for m in matched if m["values_match"]]
    matched_different = [m for m in matched if not m["values_match"]]

    summary = {
        "total_matched_attributes": len(matched),
        "matched_with_same_values": len(matched_same),
        "matched_with_different_values": len(matched_different),
        "critical_differences_count": len(critical_differences),
        "unique_to_host_a": len(only_a),
        "unique_to_host_b": len(only_b),
        "note": (
            "Different BIOS versions may use different attribute naming schemes. "
            "This comparison uses semantic matching to align equivalent settings."
        ),
    }

    return {
        "matched": matched,
        "critical_differences": critical_differences,
        "only_a": only_a,
        "only_b": only_b,
        "summary": summary,
        "counts": {
            "matched": len(matched),
            "matched_same": len(matched_same),
            "matched_different": len(matched_different),
            "critical_differences": len(critical_differences),
            "only_a": len(only_a),
            "only_b": len(only_b),
        },
    }
