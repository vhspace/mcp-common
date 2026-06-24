from __future__ import annotations

from typing import Any

from ._util import norm

TARGET_ALIASES: dict[str, list[str]] = {
    # requested -> possible Redfish values
    "bios": ["BiosSetup", "Bios", "Setup", "UefiBiosSetup"],
    "biossetup": ["BiosSetup", "Bios", "Setup", "UefiBiosSetup"],
    "setup": ["BiosSetup", "Bios", "Setup", "UefiBiosSetup"],
    "pxe": ["Pxe", "UefiHttp", "UefiPXE", "UefiPxe", "Network"],
    "network": ["Pxe", "Network", "UefiHttp", "UefiPXE", "UefiPxe"],
    "hdd": ["Hdd", "HardDrive"],
    "disk": ["Hdd", "HardDrive"],
    "cd": ["Cd", "Cdrom", "CdDvd", "DVD"],
}


def pick_target(requested: str, allowable: list[str] | None) -> tuple[str, list[str] | None]:
    """Pick BootSourceOverrideTarget tolerant of vendor naming differences.

    Returns:
      (chosen, attempted_candidates_or_none)
    """
    req = norm(requested)
    candidates = TARGET_ALIASES.get(req, [])
    if not candidates:
        candidates = [requested]

    if not allowable:
        return candidates[0], None

    allowable_norm = {norm(a): a for a in allowable}

    for cand in candidates:
        key = norm(cand)
        if key in allowable_norm:
            return allowable_norm[key], candidates

    if req in allowable_norm:
        return allowable_norm[req], candidates

    return allowable[0], candidates


def get_allowable_targets(system_json: dict[str, Any]) -> list[str] | None:
    boot = system_json.get("Boot") or {}
    for container in (boot, system_json):
        v = container.get("BootSourceOverrideTarget@Redfish.AllowableValues")
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return v
    for k in list(boot.keys()):
        if "AllowableValues" in k and "BootSourceOverrideTarget" in k:
            v = boot.get(k)
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                return v
    return None
