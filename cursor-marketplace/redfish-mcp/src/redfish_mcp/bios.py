from __future__ import annotations

from typing import Any

from .redfish import RedfishClient, RedfishEndpoint, to_abs


def discover_bios_settings_url(
    c: RedfishClient, ep: RedfishEndpoint
) -> tuple[str | None, str, dict[str, Any] | None]:
    """Return (settings_url, bios_url, bios_json) best-effort.

    Supermicro/AMI commonly exposes a writable BIOS settings object at:
      /redfish/v1/Systems/1/Bios/Settings
    or via @Redfish.Settings.SettingsObject.@odata.id on /Bios.
    """
    bios_url = f"{ep.system_url}/Bios"
    bios, bios_err = c.get_json_maybe(bios_url)
    if bios_err or not bios:
        return None, bios_url, bios

    rs = bios.get("@Redfish.Settings")
    if isinstance(rs, dict):
        so = rs.get("SettingsObject")
        if isinstance(so, dict) and isinstance(so.get("@odata.id"), str):
            return to_abs(c.base_url, so["@odata.id"]), bios_url, bios

    s = bios.get("Settings")
    if isinstance(s, dict) and isinstance(s.get("@odata.id"), str):
        return to_abs(c.base_url, s["@odata.id"]), bios_url, bios

    # Common fallback (may 404 on some firmware)
    guess = f"{bios_url}/Settings"
    _, gerr = c.get_json_maybe(guess)
    if gerr is None:
        return guess, bios_url, bios

    return None, bios_url, bios
