"""Firmware update checker using web search and extraction."""

from __future__ import annotations

import re
from typing import Any


def extract_bios_version_from_text(text: str) -> str | None:
    """Extract BIOS version from text (e.g., '3.8a' from 'H13DSG-O-CPU_3.8a_AS01.04.07')."""
    # Common patterns:
    # - "BIOS Revision: 3.8a"
    # - "3.8a" in filename
    # - "Ver 3.8a"

    # Try explicit BIOS Revision pattern
    match = re.search(r"BIOS\s+Revision:\s*([0-9]+\.[0-9]+[a-z]?)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # Try version in filename pattern (H13DSG-O-CPU_3.8a_...)
    match = re.search(r"_([0-9]+\.[0-9]+[a-z]?)_", text)
    if match:
        return match.group(1)

    # Try "Ver X.X" pattern
    match = re.search(r"Ver\s+([0-9]+\.[0-9]+[a-z]?)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def compare_versions(current: str, latest: str) -> str:
    """Compare two version strings. Returns 'older', 'same', 'newer', or 'unknown'."""
    if current == latest:
        return "same"

    # Try to parse versions like "3.7a" and "3.8a"
    def parse_version(v: str) -> tuple[int, int, str]:
        match = re.match(r"([0-9]+)\.([0-9]+)([a-z]?)", v)
        if not match:
            return (0, 0, "")
        major = int(match.group(1))
        minor = int(match.group(2))
        suffix = match.group(3) or ""
        return (major, minor, suffix)

    try:
        curr_parts = parse_version(current)
        latest_parts = parse_version(latest)

        # Compare major.minor
        if curr_parts[:2] < latest_parts[:2]:
            return "older"
        if curr_parts[:2] > latest_parts[:2]:
            return "newer"

        # Same major.minor, compare suffix
        if curr_parts[2] < latest_parts[2]:
            return "older"
        if curr_parts[2] > latest_parts[2]:
            return "newer"

        return "same"
    except Exception:
        return "unknown"


async def check_supermicro_bios_online(
    model: str, motherboard: str | None, tavily_search_fn: Any, tavily_extract_fn: Any
) -> dict[str, Any]:
    """Check Supermicro website for latest BIOS using Tavily.

    Args:
        model: System model (e.g., "PIO-8125GS-TNHR-NODE")
        motherboard: Motherboard model if known (e.g., "H13DSG-O-CPU-D")
        tavily_search_fn: Tavily search function (from MCP)
        tavily_extract_fn: Tavily extract function (from MCP)

    Returns:
        {
            "ok": bool,
            "latest_version": str | None,
            "download_url": str | None,
            "release_notes_url": str | None,
            "extracted_from": str,
            "error": str | None
        }
    """
    result: dict[str, Any] = {
        "ok": False,
        "latest_version": None,
        "download_url": None,
        "release_notes_url": None,
        "extracted_from": None,
        "error": None,
    }

    try:
        # Determine motherboard model
        # PIO-8125GS-TNHR-NODE uses H13DSG-O-CPU-D motherboard
        mb = motherboard
        if not mb and "PIO-8125GS" in model:
            mb = "H13DSG-O-CPU-D"

        if not mb:
            result["error"] = "Motherboard model unknown. Cannot construct download URL."
            return result

        # Construct Supermicro download center URL
        download_url = (
            f"https://www.supermicro.com/en/support/resources/downloadcenter/firmware/MBD-{mb}/BIOS"
        )
        result["download_url"] = download_url

        # Extract content from download page
        extract_result = await tavily_extract_fn(
            urls=[download_url], query="BIOS version revision release", extract_depth="advanced"
        )

        if not extract_result.get("results"):
            result["error"] = "Failed to extract content from Supermicro download page"
            return result

        content = extract_result["results"][0].get("raw_content", "")
        result["extracted_from"] = download_url

        # Extract BIOS version
        latest_version = extract_bios_version_from_text(content)
        if latest_version:
            result["ok"] = True
            result["latest_version"] = latest_version

            # Try to find release notes link
            if "release notes" in content.lower():
                notes_match = re.search(r'(https?://[^\s"]+release[^\s"]*)', content, re.IGNORECASE)
                if notes_match:
                    result["release_notes_url"] = notes_match.group(1)
        else:
            result["error"] = "Could not extract BIOS version from page content"

    except Exception as e:
        result["error"] = f"Exception during online check: {e}"

    return result


def get_motherboard_from_model(model: str) -> str | None:
    """Map system model to motherboard model."""
    # Known mappings
    mappings = {
        "PIO-8125GS-TNHR-NODE": "H13DSG-O-CPU-D",
        # Add more as we discover them
    }

    for model_pattern, mb in mappings.items():
        if model_pattern in model or model in model_pattern:
            return mb

    return None
