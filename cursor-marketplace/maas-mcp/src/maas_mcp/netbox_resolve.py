"""Resolve NetBox device names to MAAS ``system_id`` with explicit failure reasons."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import httpx

from maas_mcp.config import Settings
from maas_mcp.maas_client import MaasRestClient
from maas_mcp.netbox_client import NetboxClient

logger = logging.getLogger(__name__)


class NetboxResolveFailureKind(StrEnum):
    """Why NetBox-assisted resolution did not yield a MAAS system_id."""

    NOT_CONFIGURED = "not_configured"
    SETTINGS_ERROR = "settings_error"
    DEVICE_NOT_FOUND = "device_not_found"
    NO_PROVIDER_MACHINE_ID = "no_provider_machine_id"
    MAAS_NO_MACHINE_FOR_HOSTNAME = "maas_no_machine_for_hostname"
    MAAS_SYSTEM_ID_MISSING = "maas_system_id_missing"
    MAAS_QUERY_ERROR = "maas_query_error"
    NETBOX_UNAUTHORIZED = "netbox_unauthorized"
    NETBOX_FORBIDDEN = "netbox_forbidden"
    NETBOX_CLIENT_ERROR = "netbox_client_error"
    NETBOX_SERVER_ERROR = "netbox_server_error"
    NETBOX_TRANSPORT_ERROR = "netbox_transport_error"
    UNEXPECTED = "unexpected"


class NetboxResolveResult:
    """Outcome of NetBox → MAAS hostname → system_id resolution."""

    __slots__ = ("detail", "failure", "maas_hostname", "system_id")

    def __init__(
        self,
        system_id: str | None,
        failure: NetboxResolveFailureKind | None,
        detail: str | None = None,
        *,
        maas_hostname: str | None = None,
    ) -> None:
        self.system_id = system_id
        self.failure = failure
        self.detail = detail
        self.maas_hostname = maas_hostname

    @property
    def ok(self) -> bool:
        return self.failure is None and bool(self.system_id)

    @staticmethod
    def success(sid: str, *, maas_hostname: str | None = None) -> NetboxResolveResult:
        return NetboxResolveResult(sid, None, maas_hostname=maas_hostname)


def _normalize_maas_list(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and "results" in response:
        return list(response["results"])
    return [response] if response is not None else []


def _http_status_detail(exc: httpx.HTTPStatusError) -> str:
    body = ""
    try:
        body = exc.response.text[:200]
    except Exception:
        pass
    return f"HTTP {exc.response.status_code} {exc.request.url!s}" + (f": {body}" if body else "")


def format_netbox_resolution_hint(result: NetboxResolveResult) -> str:
    """One-line operator hint for stderr / MCP errors."""
    if result.ok or result.failure is None:
        return ""
    k = result.failure
    d = (result.detail or "").strip()
    lines = {
        NetboxResolveFailureKind.NOT_CONFIGURED: "",
        NetboxResolveFailureKind.SETTINGS_ERROR: f"NetBox settings error: {d}" if d else "",
        NetboxResolveFailureKind.DEVICE_NOT_FOUND: (
            "NetBox: no device matches this name (by name or Provider_Machine_ID)."
        ),
        NetboxResolveFailureKind.NO_PROVIDER_MACHINE_ID: (
            "NetBox: device found but custom_fields.Provider_Machine_ID is empty "
            "(cannot map to MAAS hostname)."
        ),
        NetboxResolveFailureKind.MAAS_NO_MACHINE_FOR_HOSTNAME: (
            f"No MAAS machine with hostname {result.maas_hostname!r} "
            f"(from Provider_Machine_ID). {d}".strip()
        ),
        NetboxResolveFailureKind.MAAS_SYSTEM_ID_MISSING: (
            "MAAS returned a machine record without system_id for that hostname."
        ),
        NetboxResolveFailureKind.MAAS_QUERY_ERROR: f"MAAS query failed: {d}"
        if d
        else "MAAS query failed.",
        NetboxResolveFailureKind.NETBOX_UNAUTHORIZED: (
            f"NetBox rejected credentials (401). {d}".strip()
            if d
            else "NetBox rejected credentials (401)."
        ),
        NetboxResolveFailureKind.NETBOX_FORBIDDEN: (
            f"NetBox forbidden (403). {d}".strip() if d else "NetBox forbidden (403)."
        ),
        NetboxResolveFailureKind.NETBOX_CLIENT_ERROR: f"NetBox client error: {d}"
        if d
        else "NetBox client error.",
        NetboxResolveFailureKind.NETBOX_SERVER_ERROR: f"NetBox server error: {d}"
        if d
        else "NetBox server error.",
        NetboxResolveFailureKind.NETBOX_TRANSPORT_ERROR: (
            f"NetBox unreachable: {d}" if d else "NetBox unreachable (network/timeout)."
        ),
        NetboxResolveFailureKind.UNEXPECTED: f"Unexpected error: {d}"
        if d
        else "Unexpected NetBox resolution error.",
    }
    return lines.get(k, d or k.value)


def resolve_netbox_device_to_maas_system_id(
    identifier: str,
    maas_client: MaasRestClient,
    *,
    netbox_client: NetboxClient | None = None,
    settings: Settings | None = None,
    on_resolved: Callable[[str, str, str], None] | None = None,
) -> NetboxResolveResult:
    """Look up *identifier* in NetBox, map Provider_Machine_ID → MAAS hostname → system_id.

    If *netbox_client* is set, it is used directly (MCP server with initialized client).
    Otherwise *settings* or ``Settings()`` supplies NetBox URL/token.
    """
    nb = netbox_client
    if nb is None:
        try:
            st = settings if settings is not None else Settings()
        except Exception as exc:
            return NetboxResolveResult(
                None,
                NetboxResolveFailureKind.SETTINGS_ERROR,
                str(exc),
            )
        if not st.netbox_url or not st.netbox_token:
            return NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED, None)
        try:
            nb = NetboxClient(
                url=str(st.netbox_url),
                token=st.netbox_token.get_secret_value(),
            )
        except Exception as exc:
            return NetboxResolveResult(None, NetboxResolveFailureKind.UNEXPECTED, str(exc))

    try:
        device = nb.lookup_device(identifier)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        detail = _http_status_detail(exc)
        if code == 401:
            kind = NetboxResolveFailureKind.NETBOX_UNAUTHORIZED
        elif code == 403:
            kind = NetboxResolveFailureKind.NETBOX_FORBIDDEN
        elif code >= 500:
            kind = NetboxResolveFailureKind.NETBOX_SERVER_ERROR
        else:
            kind = NetboxResolveFailureKind.NETBOX_CLIENT_ERROR
        return NetboxResolveResult(None, kind, detail)
    except httpx.RequestError as exc:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.NETBOX_TRANSPORT_ERROR,
            str(exc),
        )
    except Exception as exc:
        logger.debug("NetBox lookup_device failed for %r", identifier, exc_info=True)
        return NetboxResolveResult(None, NetboxResolveFailureKind.UNEXPECTED, str(exc))

    if not device:
        return NetboxResolveResult(None, NetboxResolveFailureKind.DEVICE_NOT_FOUND, None)

    custom_fields = device.get("custom_fields") or {}
    maas_hostname = custom_fields.get("Provider_Machine_ID")
    if not maas_hostname:
        return NetboxResolveResult(None, NetboxResolveFailureKind.NO_PROVIDER_MACHINE_ID, None)

    maas_hostname_str = str(maas_hostname).strip()
    if not maas_hostname_str:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.NO_PROVIDER_MACHINE_ID,
            "Provider_Machine_ID is blank",
        )

    try:
        machines = _normalize_maas_list(
            maas_client.get("machines", params={"hostname": maas_hostname_str})
        )
    except RuntimeError as exc:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.MAAS_QUERY_ERROR,
            str(exc),
            maas_hostname=maas_hostname_str,
        )
    except Exception as exc:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.UNEXPECTED,
            str(exc),
            maas_hostname=maas_hostname_str,
        )

    if not machines:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.MAAS_NO_MACHINE_FOR_HOSTNAME,
            None,
            maas_hostname=maas_hostname_str,
        )

    system_id = machines[0].get("system_id")
    if not system_id:
        return NetboxResolveResult(
            None,
            NetboxResolveFailureKind.MAAS_SYSTEM_ID_MISSING,
            None,
            maas_hostname=maas_hostname_str,
        )

    sid = str(system_id)
    if on_resolved:
        on_resolved(identifier, maas_hostname_str, sid)
    return NetboxResolveResult.success(sid, maas_hostname=maas_hostname_str)
