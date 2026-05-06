import json

import responses

from maas_mcp.redfish_bmc import (
    RedfishAccountRef,
    create_account,
    find_account,
    get_account_detail,
    get_account_service_info,
    patch_account,
    set_account_password,
    verify_login,
)


@responses.activate
def test_find_account_and_set_password_uses_etag() -> None:
    host = "1.2.3.4"

    responses.get(
        f"https://{host}/redfish/v1",
        json={"AccountService": {"@odata.id": "/redfish/v1/AccountService"}},
        status=200,
        headers={"ETag": 'W/"root"'},
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService",
        json={"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService/Accounts",
        json={"Members": [{"@odata.id": "/redfish/v1/AccountService/Accounts/5"}]},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService/Accounts/5",
        json={"Id": "5", "UserName": "maas", "@odata.etag": 'W/"acct5"'},
        status=200,
        headers={"ETag": 'W/"acct5"'},
    )

    seen_headers: dict[str, str] = {}

    def patch_cb(request):  # type: ignore[no-untyped-def]
        seen_headers.update(dict(request.headers))
        body = (
            json.loads(request.body.decode("utf-8"))
            if isinstance(request.body, (bytes, bytearray))
            else json.loads(request.body)
        )
        assert "Password" in body
        return (204, {}, "")

    responses.add_callback(
        responses.PATCH,
        f"https://{host}/redfish/v1/AccountService/Accounts/5",
        callback=patch_cb,
        content_type="application/json",
    )

    acct = find_account(host, "admin", "adminpass", "maas")
    assert isinstance(acct, RedfishAccountRef)
    assert acct.etag == 'W/"acct5"'

    set_account_password(
        acct,
        admin_user="admin",
        admin_password="adminpass",
        new_password="NewPassw0rd!",
        timeout_s=5,
    )
    assert "If-Match" in seen_headers
    assert seen_headers["If-Match"] == 'W/"acct5"'


@responses.activate
def test_verify_login_true_on_200() -> None:
    host = "1.2.3.4"
    responses.get(f"https://{host}/redfish/v1/Systems/1", json={"Id": "1"}, status=200)
    assert verify_login(host, "u", "p")


@responses.activate
def test_create_account_posts_to_accounts_collection() -> None:
    host = "1.2.3.4"

    responses.get(
        f"https://{host}/redfish/v1",
        json={"AccountService": {"@odata.id": "/redfish/v1/AccountService"}},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService",
        json={"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
        status=200,
    )
    responses.post(
        f"https://{host}/redfish/v1/AccountService/Accounts",
        status=201,
        json={},
    )

    create_account(
        host,
        admin_user="admin",
        admin_password="adminpass",
        username="maas",
        password="NewPassw0rd!",
        role_id="Administrator",
    )


def _setup_account_service_responses(host: str) -> None:
    """Register standard AccountService mock responses for multiple tests."""
    responses.get(
        f"https://{host}/redfish/v1",
        json={"AccountService": {"@odata.id": "/redfish/v1/AccountService"}},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService",
        json={
            "Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"},
            "AccountLockoutThreshold": 3,
            "AccountLockoutDuration": 30,
        },
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService/Accounts",
        json={"Members": [{"@odata.id": "/redfish/v1/AccountService/Accounts/5"}]},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService/Accounts/5",
        json={
            "Id": "5",
            "UserName": "maas",
            "RoleId": "Administrator",
            "Enabled": True,
            "Locked": False,
            "AccountTypes": ["Redfish"],
        },
        status=200,
    )


@responses.activate
def test_get_account_detail_returns_full_info() -> None:
    host = "1.2.3.4"
    _setup_account_service_responses(host)

    detail = get_account_detail(host, "admin", "adminpass", "maas")
    assert detail["UserName"] == "maas"
    assert detail["RoleId"] == "Administrator"
    assert detail["AccountTypes"] == ["Redfish"]
    assert detail["_odata_id"] == "/redfish/v1/AccountService/Accounts/5"


@responses.activate
def test_get_account_service_info_returns_lockout() -> None:
    host = "1.2.3.4"
    responses.get(
        f"https://{host}/redfish/v1",
        json={"AccountService": {"@odata.id": "/redfish/v1/AccountService"}},
        status=200,
    )
    responses.get(
        f"https://{host}/redfish/v1/AccountService",
        json={
            "AccountLockoutThreshold": 5,
            "AccountLockoutDuration": 60,
            "Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"},
        },
        status=200,
    )

    info = get_account_service_info(host, "admin", "adminpass")
    assert info["AccountLockoutThreshold"] == 5
    assert info["AccountLockoutDuration"] == 60


@responses.activate
def test_patch_account_sends_payload() -> None:
    host = "1.2.3.4"
    acct_url = "/redfish/v1/AccountService/Accounts/5"

    seen_body: dict[str, list[str]] = {}

    def patch_cb(request):  # type: ignore[no-untyped-def]
        body = json.loads(request.body) if request.body else {}
        seen_body.update(body)
        return (200, {}, json.dumps({}))

    responses.add_callback(
        responses.PATCH,
        f"https://{host}{acct_url}",
        callback=patch_cb,
        content_type="application/json",
    )

    resp = patch_account(
        host,
        acct_url,
        "admin",
        "adminpass",
        {"AccountTypes": ["IPMI", "Redfish"]},
    )
    assert resp.status_code == 200
    assert seen_body["AccountTypes"] == ["IPMI", "Redfish"]
