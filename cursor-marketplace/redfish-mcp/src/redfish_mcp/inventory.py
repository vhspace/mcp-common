from __future__ import annotations

from typing import Any

from .redfish import RedfishClient, RedfishEndpoint, filter_host_chassis, to_abs


def extract_odata_ids(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("Members"), list):
            return extract_odata_ids(obj["Members"])
        if "@odata.id" in obj and isinstance(obj["@odata.id"], str):
            out.append(obj["@odata.id"])
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out.extend(extract_odata_ids(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(extract_odata_ids(item))
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def looks_nvme_drive(drive: dict[str, Any]) -> bool:
    proto = str(drive.get("Protocol", "")).lower()
    model = str(drive.get("Model", "")).lower()
    name = str(drive.get("Name", "")).lower()
    return ("nvme" in proto) or ("nvme" in model) or ("nvme" in name)


def collect_drive_inventory(
    c: RedfishClient, ep: RedfishEndpoint, *, nvme_only: bool
) -> dict[str, Any]:
    """Best-effort drive inventory, tolerant of firmware quirks.

    Returns a dict suitable for JSON output with:
    - drives: list of drive summaries
    - sources: list of probed endpoints + errors
    - count: number of returned drives
    """
    out: dict[str, Any] = {
        "drives": [],
        "count": 0,
        "sources": [],
        "errors": [],
    }

    seen: set[str] = set()

    def add_drive(drive_odata_id: str, source: str) -> None:
        url = to_abs(c.base_url, drive_odata_id)
        if url in seen:
            return
        seen.add(url)
        d, derr = c.get_json_maybe(url)
        out["sources"].append(
            {"type": "drive", "url": url, "ok": derr is None, "error": derr, "source": source}
        )
        if derr or not d:
            return
        if nvme_only and not looks_nvme_drive(d):
            return
        out["drives"].append(
            {
                "url": url,
                "source": source,
                "Id": d.get("Id"),
                "Name": d.get("Name"),
                "Model": d.get("Model"),
                "SerialNumber": d.get("SerialNumber"),
                "CapacityBytes": d.get("CapacityBytes"),
                "Protocol": d.get("Protocol"),
                "MediaType": d.get("MediaType"),
                "Status": d.get("Status"),
                "Location": d.get("Location"),
            }
        )

    storage_root_url = f"{ep.system_url}/Storage"
    storage_root, storage_err = c.get_json_maybe(storage_root_url)
    out["sources"].append(
        {
            "type": "storage_root",
            "url": storage_root_url,
            "ok": storage_err is None,
            "error": storage_err,
        }
    )

    storage_members = extract_odata_ids(storage_root or {})
    storage_members = sorted(storage_members, key=lambda x: ("nvme" not in x.lower(), x))

    for member in storage_members:
        member_url = to_abs(c.base_url, member)
        st, sterr = c.get_json_maybe(member_url)
        out["sources"].append(
            {"type": "storage_member", "url": member_url, "ok": sterr is None, "error": sterr}
        )
        if not st:
            continue

        drives_arr = st.get("Drives") or []
        if isinstance(drives_arr, list):
            for dref in drives_arr:
                if isinstance(dref, dict) and isinstance(dref.get("@odata.id"), str):
                    add_drive(dref["@odata.id"], source=f"{member_url} (Drives array)")

        drives_coll_url = f"{member_url}/Drives"
        drives_coll, dcerr = c.get_json_maybe(drives_coll_url)
        out["sources"].append(
            {
                "type": "storage_member_drives",
                "url": drives_coll_url,
                "ok": dcerr is None,
                "error": dcerr,
            }
        )
        for did in extract_odata_ids(drives_coll or {}):
            add_drive(did, source=f"{member_url}/Drives")

        links = st.get("Links") or {}
        enclosures = links.get("Enclosures") or []
        if isinstance(enclosures, list):
            for enc in enclosures:
                if not isinstance(enc, dict) or not isinstance(enc.get("@odata.id"), str):
                    continue
                enc_url = to_abs(c.base_url, enc["@odata.id"])
                enc_obj, enc_err = c.get_json_maybe(enc_url)
                out["sources"].append(
                    {"type": "enclosure", "url": enc_url, "ok": enc_err is None, "error": enc_err}
                )
                if not enc_obj:
                    continue
                enc_drives_url = f"{enc_url}/Drives"
                enc_drives, ederr = c.get_json_maybe(enc_drives_url)
                out["sources"].append(
                    {
                        "type": "enclosure_drives",
                        "url": enc_drives_url,
                        "ok": ederr is None,
                        "error": ederr,
                    }
                )
                for did in extract_odata_ids(enc_drives or {}):
                    add_drive(did, source=f"{enc_url}/Drives")

    if not out["drives"]:
        chassis_root_url = f"{c.base_url}/redfish/v1/Chassis"
        chassis_root, cherr = c.get_json_maybe(chassis_root_url)
        out["sources"].append(
            {"type": "chassis_root", "url": chassis_root_url, "ok": cherr is None, "error": cherr}
        )
        for ch in extract_odata_ids(filter_host_chassis((chassis_root or {}).get("Members", []))):
            ch_url = to_abs(c.base_url, ch)
            if nvme_only and "nvme" not in ch_url.lower():
                continue
            drives_url = f"{ch_url}/Drives"
            drives_obj, dserr = c.get_json_maybe(drives_url)
            out["sources"].append(
                {"type": "chassis_drives", "url": drives_url, "ok": dserr is None, "error": dserr}
            )
            for did in extract_odata_ids(drives_obj or {}):
                add_drive(did, source=f"{ch_url}/Drives")

    def key(x: dict[str, Any]) -> tuple[int, str]:
        cap = x.get("CapacityBytes")
        try:
            cap_i = int(cap) if cap is not None else 0
        except Exception:
            cap_i = 0
        return (-cap_i, str(x.get("SerialNumber") or ""))

    out["drives"] = sorted(out["drives"], key=key)
    out["count"] = len(out["drives"])
    return out
