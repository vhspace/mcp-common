"""VGA framebuffer capture for BMCs (Supermicro, Dell iDRAC, generic Redfish).

Capture methods (tried in 'auto' order):
  1. Redfish OEM DumpService (Supermicro fw >= 4.0) -- preferred
  2. CGI CapturePreview (Supermicro older fw) -- cookie-based fallback
  3. Dell iDRAC sysmgmt preview + OEM ExportServerScreenShot (iDRAC 9/10)

BMCs often lie about Content-Type (send application/octet-stream for JPEG),
so we sniff magic bytes via _sniff_mime().
"""

from __future__ import annotations

import logging
import re
import time
from typing import Literal

import requests

from .redfish import RedfishClient

logger = logging.getLogger("redfish_mcp.screen_capture")

IDRAC_MANAGER_PATH = "/redfish/v1/Managers/iDRAC.Embedded.1"

DELL_OEM_SCREENSHOT_ACTION = "DellLCService/Actions/DellLCService.ExportServerScreenShot"
DELL_OEM_PREFIX_IDRAC10 = f"{IDRAC_MANAGER_PATH}/Oem/Dell"
DELL_OEM_PREFIX_IDRAC9 = "/redfish/v1/Dell/Managers/iDRAC.Embedded.1"


def _suppress_tls_warnings() -> None:
    """Suppress urllib3 InsecureRequestWarning for self-signed BMC certs."""
    from mcp_common.logging import suppress_ssl_warnings

    suppress_ssl_warnings()


DUMP_SERVICE_ACTION = "/redfish/v1/Oem/Supermicro/DumpService/Actions/OemDumpService.Collect"

DumpType = Literal["ScreenCapture", "VideoCapture", "CrashScreenCapture"]


def _sniff_mime(data: bytes, header_ct: str) -> str:
    """Detect image format from magic bytes since BMC returns 'application/octet-stream'."""
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 2 and data[:2] == b"BM":
        return "image/bmp"
    if "image/" in header_ct:
        return header_ct.split(";")[0].strip()
    return "image/jpeg"


def vendor_from_model(model: str) -> str | None:
    """Infer BMC vendor from a system model string (e.g. from Redfish Systems).

    This provides a fast, auth-free vendor signal when detect_vendor() cannot
    reach the Redfish root (timeout, 401, etc.).  Returns None when the model
    is unrecognized.
    """
    if not model:
        return None
    m = model.lower()
    if m.startswith("poweredge") or "idrac" in m:
        return "dell"
    if "supermicro" in m or m.startswith("sys-") or m.startswith("as-"):
        return "supermicro"
    if "proliant" in m or "synergy" in m:
        return "hpe"
    return None


def vendor_from_manufacturer(manufacturer: str) -> str | None:
    """Infer BMC vendor from a system Manufacturer string."""
    if not manufacturer:
        return None
    m = manufacturer.lower()
    if "dell" in m:
        return "dell"
    if "supermicro" in m:
        return "supermicro"
    if "hpe" in m or "hewlett" in m:
        return "hpe"
    if "gigabyte" in m or "giga computing" in m:
        return "gigabyte"
    return None


def detect_vendor(client: RedfishClient) -> str:
    """Detect BMC vendor from Redfish root endpoint.

    Returns: "supermicro", "dell", "hpe", "gigabyte", or "unknown"
    """
    try:
        root = client.get_json(f"{client.base_url}/redfish/v1")
        oem_keys = set(root.get("Oem", {}).keys())
        if "Supermicro" in oem_keys:
            return "supermicro"
        if "Dell" in oem_keys:
            return "dell"
        if "Hpe" in oem_keys or "HPE" in oem_keys:
            return "hpe"
        if "Gbt" in oem_keys or "Ami" in oem_keys:
            return "gigabyte"
        product = root.get("Product", "").lower()
        vendor = root.get("Vendor", "").lower()
        if "supermicro" in product:
            return "supermicro"
        if "idrac" in product or "dell" in product:
            return "dell"
        if "ami" in product or "ami" in vendor or "giga computing" in vendor:
            return "gigabyte"
    except Exception:
        pass
    return "unknown"


def detect_idrac_generation(client: RedfishClient) -> str:
    """Detect iDRAC generation from Manager firmware version.

    iDRAC10 firmware versions start with 7.x; iDRAC9 is 6.x and below.
    Falls back to probing the OEM path structure when the version string
    is unavailable or ambiguous.

    Returns: "idrac10", "idrac9", or "unknown"
    """
    mgr_url = f"{client.base_url}{IDRAC_MANAGER_PATH}"
    data, _err = client.get_json_maybe(mgr_url)
    if data is not None:
        fw_ver = data.get("FirmwareVersion", "")
        match = re.match(r"(\d+)\.", fw_ver)
        if match:
            major = int(match.group(1))
            if major >= 7:
                return "idrac10"
            return "idrac9"

    # Version unavailable -- probe the OEM path structure
    idrac10_probe = f"{client.base_url}{DELL_OEM_PREFIX_IDRAC10}"
    probe_data, _probe_err = client.get_json_maybe(idrac10_probe)
    if probe_data is not None:
        return "idrac10"
    idrac9_probe = f"{client.base_url}{DELL_OEM_PREFIX_IDRAC9}"
    probe_data, _probe_err = client.get_json_maybe(idrac9_probe)
    if probe_data is not None:
        return "idrac9"
    return "unknown"


def _dell_oem_screenshot_url(base: str, generation: str) -> list[str]:
    """Return ordered OEM screenshot URLs to try for the given iDRAC generation."""
    idrac10_url = f"{base}{DELL_OEM_PREFIX_IDRAC10}/{DELL_OEM_SCREENSHOT_ACTION}"
    idrac9_url = f"{base}{DELL_OEM_PREFIX_IDRAC9}/{DELL_OEM_SCREENSHOT_ACTION}"
    if generation == "idrac9":
        return [idrac9_url, idrac10_url]
    # idrac10 or unknown -- try the newer path first
    return [idrac10_url, idrac9_url]


class DellPrivilegeError(RuntimeError):
    """Raised when iDRAC denies a screenshot due to VirtualConsole privilege (LC081)."""


def _check_dell_privilege_error(resp: requests.Response) -> None:
    """Raise DellPrivilegeError if the response indicates LC081 privilege denial."""
    if resp.status_code != 400:
        return
    try:
        body = resp.text
    except Exception:
        return
    if "LC081" in body or "VirtualConsole.1.AccessPrivilege" in body:
        raise DellPrivilegeError(
            "iDRAC screenshot denied: BMC user lacks VirtualConsole privilege (LC081). "
            "Grant 'Virtual Console' access to the BMC user account or use a different account."
        )


_SCREENSHOT_SUPPORTED_VENDORS = frozenset({"supermicro", "dell", "gigabyte"})


def vendor_methods(vendor: str) -> list[str]:
    """Return ordered capture methods appropriate for *vendor*."""
    if vendor == "supermicro":
        return ["redfish", "cgi"]
    if vendor == "dell":
        return ["dell"]
    if vendor == "gigabyte":
        return ["ami"]
    if vendor != "unknown":
        return []
    return ["redfish", "cgi", "dell"]


def is_screenshot_supported(vendor: str) -> bool:
    """Return True if screenshot capture is supported for *vendor*."""
    return vendor in _SCREENSHOT_SUPPORTED_VENDORS or vendor == "unknown"


def capture_screen_redfish(client: RedfishClient) -> tuple[bytes, str]:
    """Capture VGA framebuffer via Redfish OEM DumpService.

    Two-step: POST Create, then POST Download.
    Returns (image_bytes, mime_type).
    """
    url = f"{client.base_url}{DUMP_SERVICE_ACTION}"

    create_resp = client.session.post(
        url,
        json={"DumpType": "ScreenCapture", "ActionType": "Create"},
        headers={"Content-Type": "application/json"},
        timeout=client.timeout_s,
    )
    create_resp.raise_for_status()

    dl_resp = client.session.post(
        url,
        json={"DumpType": "ScreenCapture", "ActionType": "Download"},
        headers={"Content-Type": "application/json"},
        timeout=client.timeout_s,
    )
    dl_resp.raise_for_status()

    if len(dl_resp.content) < 256:
        raise RuntimeError(
            f"DumpService returned unexpectedly small payload ({len(dl_resp.content)} bytes); "
            f"body: {dl_resp.text[:200]}"
        )

    mime = _sniff_mime(dl_resp.content, dl_resp.headers.get("Content-Type", ""))
    return dl_resp.content, mime


def capture_screen_cgi(
    host: str,
    user: str,
    password: str,
    verify_tls: bool,
    timeout_s: int,
) -> tuple[bytes, str]:
    """Capture VGA framebuffer via the BMC CGI web interface (older firmware).

    Three-step: POST login, GET CapturePreview trigger, GET image download.
    Returns (image_bytes, mime_type).
    """
    base = f"https://{host}"
    sess = requests.Session()
    sess.verify = verify_tls
    if not verify_tls:
        _suppress_tls_warnings()

    login_resp = sess.post(
        f"{base}/cgi/login.cgi",
        data={"name": user, "pwd": password},
        timeout=timeout_s,
    )
    login_resp.raise_for_status()

    sid = sess.cookies.get("SID")
    if not sid:
        raise RuntimeError("CGI login did not return SID cookie")

    ts = time.strftime("%a %d %b %Y %H:%M:%S GMT", time.gmtime())
    sess.get(
        f"{base}/cgi/CapturePreview.cgi?IKVM_PREVIEW_XML=(0,0)&time_stamp={ts}",
        timeout=timeout_s,
    )

    dl_resp = sess.get(
        f"{base}/cgi/url_redirect.cgi?url_name=Snapshot&url_type=img",
        timeout=timeout_s,
    )
    dl_resp.raise_for_status()

    if len(dl_resp.content) < 256:
        raise RuntimeError(
            f"CGI snapshot returned unexpectedly small payload ({len(dl_resp.content)} bytes)"
        )

    try:
        sess.get(f"{base}/cgi/logout.cgi", timeout=5)
    except Exception:
        pass

    mime = _sniff_mime(dl_resp.content, dl_resp.headers.get("Content-Type", ""))
    return dl_resp.content, mime


def capture_screen_dell(
    host: str,
    user: str,
    password: str,
    verify_tls: bool,
    timeout_s: int,
    *,
    idrac_generation: str = "unknown",
) -> tuple[bytes, str]:
    """Capture VGA framebuffer from Dell iDRAC (9/10).

    Uses the iDRAC sysmgmt preview API first, then falls back to the
    Redfish OEM ExportServerScreenShot action.  The OEM path differs
    between iDRAC9 and iDRAC10; both are tried in generation-appropriate
    order.

    Raises DellPrivilegeError if the BMC returns LC081 (VirtualConsole
    privilege denied).
    """
    base = f"https://{host}"
    sess = requests.Session()
    sess.auth = (user, password)
    sess.verify = verify_tls
    if not verify_tls:
        _suppress_tls_warnings()

    preview_url = f"{base}/sysmgmt/2015/server/preview"
    resp = sess.get(preview_url, timeout=timeout_s)

    if resp.status_code == 200 and len(resp.content) >= 256:
        mime = _sniff_mime(resp.content, resp.headers.get("Content-Type", ""))
        return resp.content, mime

    oem_urls = _dell_oem_screenshot_url(base, idrac_generation)
    last_oem_resp: requests.Response | None = None
    for oem_url in oem_urls:
        oem_resp = sess.post(
            oem_url,
            json={"FileType": "ServerScreenShot"},
            headers={"Content-Type": "application/json"},
            timeout=timeout_s,
        )
        _check_dell_privilege_error(oem_resp)
        if oem_resp.status_code < 400 and len(oem_resp.content) >= 256:
            mime = _sniff_mime(oem_resp.content, oem_resp.headers.get("Content-Type", ""))
            return oem_resp.content, mime
        last_oem_resp = oem_resp

    oem_status = last_oem_resp.status_code if last_oem_resp else "N/A"
    raise RuntimeError(
        f"Dell screenshot failed: sysmgmt={resp.status_code} "
        f"({len(resp.content)} bytes), OEM={oem_status}"
    )


def try_capture(
    host: str,
    user: str,
    password: str,
    method: str = "auto",
    verify_tls: bool = False,
    timeout_s: int = 30,
    *,
    vendor_hint: str | None = None,
) -> tuple[bytes, str, str]:
    """Try available capture methods in order. Returns (bytes, mime, method_used).

    When *method* is ``"auto"``, queries the Redfish root to detect the BMC
    vendor and only tries methods relevant to that vendor.

    *vendor_hint* (optional) short-circuits auto-detection when provided (e.g.
    derived from the system model string).  This avoids wasting 30s+ on
    Supermicro-specific endpoints when the host is known to be Dell.

    Raises RuntimeError if all methods fail.
    """
    vendor = "unknown"
    idrac_gen = "unknown"
    if method == "auto":
        if vendor_hint and vendor_hint != "unknown":
            vendor = vendor_hint
        else:
            try:
                c_detect = RedfishClient(
                    host=host,
                    user=user,
                    password=password,
                    verify_tls=verify_tls,
                    timeout_s=timeout_s,
                )
                vendor = detect_vendor(c_detect)
            except Exception:
                pass
        if vendor == "dell":
            try:
                c_dell = RedfishClient(
                    host=host,
                    user=user,
                    password=password,
                    verify_tls=verify_tls,
                    timeout_s=timeout_s,
                )
                idrac_gen = detect_idrac_generation(c_dell)
            except Exception:
                pass
        methods = vendor_methods(vendor)
    else:
        methods = [method]

    errors: list[str] = []
    for m in methods:
        try:
            if m == "redfish":
                c = RedfishClient(
                    host=host,
                    user=user,
                    password=password,
                    verify_tls=verify_tls,
                    timeout_s=timeout_s,
                )
                img, mime = capture_screen_redfish(c)
                return img, mime, "redfish"
            elif m == "cgi":
                img, mime = capture_screen_cgi(host, user, password, verify_tls, timeout_s)
                return img, mime, "cgi"
            elif m == "dell":
                img, mime = capture_screen_dell(
                    host,
                    user,
                    password,
                    verify_tls,
                    timeout_s,
                    idrac_generation=idrac_gen,
                )
                return img, mime, "dell"
            elif m == "ami":
                import asyncio

                from redfish_mcp.kvm.backends.playwright_ami import capture_screen_ami

                coro = capture_screen_ami(host, user, password, timeout_s=timeout_s)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    raise RuntimeError(
                        "try_capture() with method='ami' cannot be called from an async "
                        "context. Use 'await capture_screen_ami(...)' directly."
                    )
                img, mime = asyncio.run(coro)
                return img, mime, "ami"
        except DellPrivilegeError:
            raise
        except Exception as e:
            errors.append(f"{m}: {e}")
            continue

    vendor_info = f" (detected vendor: {vendor})" if vendor != "unknown" else ""
    raise RuntimeError(f"All capture methods failed{vendor_info}: {'; '.join(errors)}")


def download_dump_redfish(client: RedfishClient, dump_type: DumpType) -> tuple[bytes, str]:
    """Download a VideoCapture or CrashScreenCapture from DumpService.

    Unlike ScreenCapture, these don't need a Create step -- they're
    pre-recorded by the BMC (if enabled).
    Returns (file_bytes, content_type).
    """
    url = f"{client.base_url}{DUMP_SERVICE_ACTION}"
    dl_resp = client.session.post(
        url,
        json={"DumpType": dump_type, "ActionType": "Download"},
        headers={"Content-Type": "application/json"},
        timeout=client.timeout_s,
    )
    dl_resp.raise_for_status()
    ct = dl_resp.headers.get("Content-Type", "application/octet-stream")
    return dl_resp.content, ct
