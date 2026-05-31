"""Tests for maas_check_bmc_health and BMC account management improvements."""

from unittest.mock import AsyncMock

from maas_mcp import server


def _make_ctx():
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.debug = AsyncMock()
    return ctx


class FakeClient:
    def __init__(self, power_params: dict) -> None:  # type: ignore[type-arg]
        self._power = power_params

    def get(self, path: str, params: dict | None = None):  # type: ignore[type-arg,no-untyped-def]
        if params == {"op": "power_parameters"}:
            return self._power
        if path.startswith("machines/") and params is None:
            return {"system_id": path.split("/")[1], "hostname": "test"}
        raise AssertionError(f"Unexpected get: {path} {params}")

    def put(self, path: str, data: dict):  # type: ignore[type-arg,no-untyped-def]
        return {"ok": True}


async def test_check_bmc_health_healthy(monkeypatch) -> None:
    """Full healthy BMC returns no issues."""
    power = {
        "power_address": "10.0.0.1",
        "power_user": "maas",
        "power_pass": "secret",
        "privilege_level": "ADMINISTRATOR",
    }
    monkeypatch.setattr(server, "get_client", lambda _: FakeClient(power))
    monkeypatch.setattr(
        server,
        "get_account_detail",
        lambda host, admin, passwd, user, timeout_s=20: {
            "_odata_id": "/redfish/v1/AccountService/Accounts/5",
            "_etag": None,
            "UserName": "maas",
            "RoleId": "Administrator",
            "Enabled": True,
            "Locked": False,
            "AccountTypes": ["IPMI", "Redfish"],
        },
    )
    monkeypatch.setattr(
        server,
        "get_account_service_info",
        lambda host, admin, passwd, timeout_s=20: {
            "AccountLockoutThreshold": 5,
            "AccountLockoutDuration": 60,
        },
    )
    monkeypatch.setattr(server, "verify_login", lambda *a, **kw: True)

    result = await server.maas_check_bmc_health(
        _make_ctx(),
        instance="default",
        system_id="abc123",
        redfish_admin_user="admin",
        redfish_admin_password="adminpass",
    )

    data = result.structured_content
    assert data["healthy"] is True
    assert data["issues"] == []
    assert data["account_found"] is True
    assert data["bmc_role_id"] == "Administrator"
    assert data["password_verified"] is True


async def test_check_bmc_health_locked_and_missing_ipmi(monkeypatch) -> None:
    """Locked account with missing IPMI AccountType reports issues."""
    power = {
        "power_address": "10.0.0.1",
        "power_user": "maas",
        "power_pass": "secret",
        "privilege_level": "ADMINISTRATOR",
    }
    monkeypatch.setattr(server, "get_client", lambda _: FakeClient(power))
    monkeypatch.setattr(
        server,
        "get_account_detail",
        lambda host, admin, passwd, user, timeout_s=20: {
            "_odata_id": "/redfish/v1/AccountService/Accounts/5",
            "_etag": None,
            "UserName": "maas",
            "RoleId": "Operator",
            "Enabled": True,
            "Locked": True,
            "AccountTypes": ["Redfish"],
        },
    )
    monkeypatch.setattr(
        server,
        "get_account_service_info",
        lambda host, admin, passwd, timeout_s=20: {
            "AccountLockoutThreshold": 3,
            "AccountLockoutDuration": 30,
        },
    )
    monkeypatch.setattr(server, "verify_login", lambda *a, **kw: False)

    result = await server.maas_check_bmc_health(
        _make_ctx(),
        instance="default",
        system_id="abc123",
        redfish_admin_user="admin",
        redfish_admin_password="adminpass",
    )

    data = result.structured_content
    assert data["healthy"] is False
    issues = data["issues"]
    assert any("LOCKED" in i for i in issues)
    assert any("IPMI" in i for i in issues)
    assert any("Role mismatch" in i or "not 'Administrator'" in i for i in issues)
    assert any("Password verification FAILED" in i for i in issues)


async def test_check_bmc_health_account_not_found(monkeypatch) -> None:
    """When account doesn't exist on BMC."""
    power = {
        "power_address": "10.0.0.1",
        "power_user": "maas",
        "power_pass": "secret",
    }
    monkeypatch.setattr(server, "get_client", lambda _: FakeClient(power))
    monkeypatch.setattr(
        server,
        "get_account_detail",
        _raise_redfish_error,
    )

    result = await server.maas_check_bmc_health(
        _make_ctx(),
        instance="default",
        system_id="abc123",
    )

    data = result.structured_content
    assert data["account_found"] is False
    assert any("not found" in i for i in data["issues"])


def _raise_redfish_error(*args, **kwargs):  # type: ignore[no-untyped-def]
    from maas_mcp.redfish_bmc import RedfishError

    raise RedfishError("Account 'maas' not found on BMC 10.0.0.1")


async def test_check_bmc_health_falls_back_to_power_creds(monkeypatch) -> None:
    """When no admin creds provided, uses power_user/power_pass."""
    power = {
        "power_address": "10.0.0.1",
        "power_user": "maas",
        "power_pass": "secret",
    }

    captured_creds: list[tuple[str, str]] = []

    def fake_detail(host, admin, passwd, user, timeout_s=20):  # type: ignore[no-untyped-def]
        captured_creds.append((admin, passwd))
        return {
            "_odata_id": "/redfish/v1/AccountService/Accounts/5",
            "_etag": None,
            "UserName": "maas",
            "RoleId": "Administrator",
            "Enabled": True,
            "Locked": False,
            "AccountTypes": ["IPMI", "Redfish"],
        }

    monkeypatch.setattr(server, "get_client", lambda _: FakeClient(power))
    monkeypatch.setattr(server, "get_account_detail", fake_detail)
    monkeypatch.setattr(
        server,
        "get_account_service_info",
        lambda host, admin, passwd, timeout_s=20: {
            "AccountLockoutThreshold": 5,
            "AccountLockoutDuration": 60,
        },
    )
    monkeypatch.setattr(server, "verify_login", lambda *a, **kw: True)

    await server.maas_check_bmc_health(_make_ctx(), instance="default", system_id="abc123")

    assert captured_creds[0] == ("maas", "secret")
