"""Named log filter presets for common Cumulus Linux troubleshooting scenarios.

Shared by both the MCP server and CLI so preset names behave identically.
"""

from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "routing": {
        "unit": "frr.service",
        "description": "BGP, OSPF, zebra — FRRouting protocols",
    },
    "switching": {
        "unit": "switchd.service",
        "description": "ASIC sync, hardware forwarding (switchd)",
    },
    "mlag": {
        "unit": "clagd.service",
        "description": "MLAG / CLAG daemon",
    },
    "platform": {
        "identifier": "smond",
        "description": "Fan, PSU, and temperature sensors",
    },
    "nvue": {
        "unit": "nvued.service",
        "description": "NVUE configuration API daemon",
    },
    "stp": {
        "unit": "mstpd.service",
        "description": "Spanning tree (MSTP/RSTP)",
    },
    "kernel": {
        "kernel": True,
        "description": "Kernel messages (dmesg equivalent)",
    },
    "all-errors": {
        "priority": "err",
        "description": "All messages at error level or above",
    },
}

FILTER_KEYS = frozenset({"unit", "identifier", "priority", "grep", "kernel", "boot"})


def resolve_preset(
    preset: str | None,
    *,
    unit: str | None = None,
    identifier: str | None = None,
    priority: str | None = None,
    grep: str | None = None,
    boot: bool = False,
    kernel: bool = False,
) -> dict[str, Any]:
    """Merge a named preset with explicit overrides.

    Explicit values always win over preset defaults.  Returns a dict with
    keys matching the ``SwitchDriver.logs()`` keyword arguments (minus
    ``lines``, ``since``, ``until`` which are always explicit).

    Raises ``ValueError`` for unknown preset names.
    """
    base: dict[str, Any] = {}
    if preset:
        if preset not in PRESETS:
            available = ", ".join(sorted(PRESETS))
            raise ValueError(f"unknown preset {preset!r} (available: {available})")
        base = {k: v for k, v in PRESETS[preset].items() if k in FILTER_KEYS}

    overrides: dict[str, Any] = {}
    if unit is not None:
        overrides["unit"] = unit
    if identifier is not None:
        overrides["identifier"] = identifier
    if priority is not None:
        overrides["priority"] = priority
    if grep is not None:
        overrides["grep"] = grep
    if boot:
        overrides["boot"] = True
    if kernel:
        overrides["kernel"] = True

    return {**base, **overrides}
