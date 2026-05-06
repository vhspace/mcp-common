"""Unit tests for NetBox -> MAAS resolution outcomes."""

from unittest.mock import MagicMock, patch

import httpx

from maas_mcp.netbox_resolve import (
    NetboxResolveFailureKind,
    NetboxResolveResult,
    format_netbox_resolution_hint,
    resolve_netbox_device_to_maas_system_id,
)


def test_format_hint_not_configured_empty() -> None:
    r = NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED)
    assert format_netbox_resolution_hint(r) == ""


def test_format_hint_device_not_found() -> None:
    r = NetboxResolveResult(None, NetboxResolveFailureKind.DEVICE_NOT_FOUND)
    assert "no device matches" in format_netbox_resolution_hint(r).lower()


def test_netbox_401() -> None:
    maas = MagicMock()
    nb = MagicMock()
    req = httpx.Request("GET", "https://nb/api/dcim/devices/")
    resp = httpx.Response(401, request=req)

    def boom(*_a: object, **_kw: object) -> None:
        raise httpx.HTTPStatusError("unauthorized", request=req, response=resp)

    nb.lookup_device.side_effect = boom

    r = resolve_netbox_device_to_maas_system_id("dev", maas, netbox_client=nb)
    assert r.failure == NetboxResolveFailureKind.NETBOX_UNAUTHORIZED
    assert "401" in (r.detail or "")


def test_netbox_403() -> None:
    maas = MagicMock()
    nb = MagicMock()
    req = httpx.Request("GET", "https://nb/api/dcim/devices/")
    resp = httpx.Response(403, request=req)

    def boom(*_a: object, **_kw: object) -> None:
        raise httpx.HTTPStatusError("forbidden", request=req, response=resp)

    nb.lookup_device.side_effect = boom

    r = resolve_netbox_device_to_maas_system_id("dev", maas, netbox_client=nb)
    assert r.failure == NetboxResolveFailureKind.NETBOX_FORBIDDEN


def test_success_calls_on_resolved() -> None:
    maas = MagicMock()
    maas.get.return_value = [{"system_id": "s1", "hostname": "gpu001"}]
    nb = MagicMock()
    nb.lookup_device.return_value = {
        "name": "n",
        "custom_fields": {"Provider_Machine_ID": "gpu001"},
    }
    seen: list[tuple[str, str, str]] = []

    r = resolve_netbox_device_to_maas_system_id(
        "tenant-name",
        maas,
        netbox_client=nb,
        on_resolved=lambda i, h, s: seen.append((i, h, s)),
    )
    assert r.ok and r.system_id == "s1"
    assert seen == [("tenant-name", "gpu001", "s1")]


def test_maas_query_runtime_error() -> None:
    maas = MagicMock()
    maas.get.side_effect = RuntimeError("MAAS GET failed: 500")
    nb = MagicMock()
    nb.lookup_device.return_value = {
        "custom_fields": {"Provider_Machine_ID": "gpu001"},
    }
    r = resolve_netbox_device_to_maas_system_id("x", maas, netbox_client=nb)
    assert r.failure == NetboxResolveFailureKind.MAAS_QUERY_ERROR
    assert "500" in (r.detail or "")


def test_not_configured_without_netbox_client() -> None:
    maas = MagicMock()
    with patch("maas_mcp.netbox_resolve.Settings") as m_set:
        st = MagicMock()
        st.netbox_url = None
        st.netbox_token = None
        m_set.return_value = st
        r = resolve_netbox_device_to_maas_system_id("x", maas)
    assert r.failure == NetboxResolveFailureKind.NOT_CONFIGURED
