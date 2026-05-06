from unittest.mock import AsyncMock

import pytest

from maas_mcp import server
from maas_mcp.redfish_bmc import RedfishAccountRef


class FakeClient:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, params: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        if path == "machines/gcwb8r" and params == {"op": "power_parameters"}:
            return {"power_address": "192.168.196.1", "power_user": "maas"}
        if path.startswith("machines/") and params is None:
            return {"system_id": path.split("/")[1], "hostname": "test"}
        raise AssertionError(f"Unexpected get call: path={path}, params={params}")

    def put(self, path: str, data: dict[str, str]):  # type: ignore[no-untyped-def]
        self.put_calls.append((path, data))
        return {"ok": True}


@pytest.mark.asyncio
async def test_password_sync_uses_internal_impl_not_tool(monkeypatch) -> None:
    """Regression: ensure the tool function is callable directly."""
    fake = FakeClient()

    monkeypatch.setattr(server, "get_client", lambda _instance: fake)
    monkeypatch.setattr(
        server,
        "find_account",
        lambda host, admin_user, admin_password, username, timeout_s=20: RedfishAccountRef(
            host=host,
            account_odata_id="/redfish/v1/AccountService/Accounts/6",
            etag=None,
        ),
    )
    monkeypatch.setattr(server, "set_account_password", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "verify_login", lambda *args, **kwargs: True)

    result = await server.maas_set_bmc_account_password_from_maas(
        ctx=AsyncMock(),
        instance="default",
        system_id="gcwb8r",
        bmc_account_username="maas",
        new_password="NewSecret123!",
        redfish_admin_user="admin",
        redfish_admin_password="adminpass",
        sync_back_to_maas=True,
        skip_power_check=True,
        allow_write=True,
    )

    data = result.structured_content
    assert data["ok"] is True
    assert data["maas_synced"] is True
    assert "ipmi_account_type" in data
    assert "lockout" in data
    assert fake.put_calls, "Expected MAAS power parameters update"
    path, data = fake.put_calls[0]
    assert path == "machines/gcwb8r"
    assert data["power_parameters_power_user"] == "maas"
    assert data["power_parameters_power_address"] == "192.168.196.1"
    assert data["power_parameters_power_pass"] == "NewSecret123!"


@pytest.mark.asyncio
async def test_password_sync_ipmi_account_type_added(monkeypatch) -> None:
    """When get_account_detail succeeds, IPMI AccountType fix is attempted."""
    fake = FakeClient()

    monkeypatch.setattr(server, "get_client", lambda _instance: fake)
    monkeypatch.setattr(
        server,
        "find_account",
        lambda host, admin_user, admin_password, username, timeout_s=20: RedfishAccountRef(
            host=host,
            account_odata_id="/redfish/v1/AccountService/Accounts/6",
            etag=None,
        ),
    )
    monkeypatch.setattr(server, "set_account_password", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "verify_login", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        server,
        "get_account_detail",
        lambda host, admin_user, admin_password, username, timeout_s=20: {
            "_odata_id": "/redfish/v1/AccountService/Accounts/6",
            "_etag": None,
            "AccountTypes": ["Redfish"],
            "RoleId": "Administrator",
        },
    )

    class FakeResp:
        status_code = 200

    monkeypatch.setattr(server, "patch_account", lambda *args, **kwargs: FakeResp())
    monkeypatch.setattr(
        server,
        "get_account_service_info",
        lambda host, admin_user, admin_password, timeout_s=20: {
            "AccountLockoutThreshold": 3,
            "AccountLockoutDuration": 30,
        },
    )

    result = await server.maas_set_bmc_account_password_from_maas(
        ctx=AsyncMock(),
        instance="default",
        system_id="gcwb8r",
        bmc_account_username="maas",
        new_password="NewSecret123!",
        redfish_admin_user="admin",
        redfish_admin_password="adminpass",
        sync_back_to_maas=False,
        allow_write=True,
    )

    data = result.structured_content
    assert data["ok"] is True
    assert data["ipmi_account_type"]["ipmi_account_type"] == "added"
    assert "IPMI" in data["ipmi_account_type"]["account_types"]
    assert data["lockout"]["lockout_threshold"] == 3
    assert "warning" in data["lockout"]
