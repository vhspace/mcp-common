"""Redfish AccountService helpers.

This module intentionally stays dependency-light (requests only) and contains
helpers used by MCP tools that need to interact with BMC user accounts.

Security notes:
- Callers must gate any write operations behind an explicit allow_write flag.
- Do not log or return passwords.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


@dataclass(frozen=True, slots=True)
class RedfishAccountRef:
    host: str
    account_odata_id: str
    etag: str | None


class RedfishError(RuntimeError):
    pass


def _url(host: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"https://{host}{path}"


def _json_or_raise(resp: requests.Response) -> dict[str, Any]:
    try:
        data: dict[str, Any] = resp.json()
        return data
    except Exception as e:  # pragma: no cover
        raise RedfishError(f"Invalid JSON response: HTTP {resp.status_code}") from e


def _get_accounts_collection_url(host: str, auth: HTTPBasicAuth, timeout_s: int = 20) -> str:
    """Resolve the Redfish AccountService Accounts collection URL."""
    root = requests.get(_url(host, "/redfish/v1"), auth=auth, verify=False, timeout=timeout_s)
    root.raise_for_status()
    root_j = _json_or_raise(root)
    acct_svc = (root_j.get("AccountService") or {}).get("@odata.id") or "/redfish/v1/AccountService"
    svc = requests.get(_url(host, acct_svc), auth=auth, verify=False, timeout=timeout_s)
    svc.raise_for_status()
    svc_j = _json_or_raise(svc)
    accounts: str | None = (svc_j.get("Accounts") or {}).get("@odata.id")
    if not accounts:
        raise RedfishError("AccountService missing Accounts collection")
    return accounts


def _iter_accounts(
    host: str, auth: HTTPBasicAuth, timeout_s: int
) -> list[tuple[str, requests.Response, dict[str, Any]]]:
    """Fetch all account members. Returns list of (odata_id, response, json)."""
    accounts = _get_accounts_collection_url(host, auth, timeout_s)
    col = requests.get(_url(host, accounts), auth=auth, verify=False, timeout=timeout_s)
    col.raise_for_status()
    col_j = _json_or_raise(col)

    results: list[tuple[str, requests.Response, dict[str, Any]]] = []
    for mem in col_j.get("Members", []) or []:
        mid = (mem or {}).get("@odata.id")
        if not mid:
            continue
        det = requests.get(_url(host, mid), auth=auth, verify=False, timeout=timeout_s)
        if det.status_code != 200:
            continue
        results.append((mid, det, _json_or_raise(det)))
    return results


def _extract_etag(resp: requests.Response, body: dict[str, Any]) -> str | None:
    return resp.headers.get("ETag") or resp.headers.get("Etag") or body.get("@odata.etag")


def find_account(
    host: str, admin_user: str, admin_password: str, username: str, *, timeout_s: int = 20
) -> RedfishAccountRef:
    """Find a Redfish account by username and return its odata id + etag (if present)."""
    auth = HTTPBasicAuth(admin_user, admin_password)
    for mid, det_resp, det_j in _iter_accounts(host, auth, timeout_s):
        uname = det_j.get("UserName") or det_j.get("Id")
        if uname == username:
            return RedfishAccountRef(
                host=host, account_odata_id=mid, etag=_extract_etag(det_resp, det_j)
            )

    raise RedfishError(f"Account '{username}' not found on BMC {host}")


def set_account_password(
    account: RedfishAccountRef,
    *,
    admin_user: str,
    admin_password: str,
    new_password: str,
    timeout_s: int = 20,
) -> None:
    """Set an account password via Redfish AccountService.

    Raises RedfishError with a sanitized message on failure.
    """
    auth = HTTPBasicAuth(admin_user, admin_password)
    headers = {"Content-Type": "application/json"}
    if account.etag:
        headers["If-Match"] = account.etag

    resp = requests.patch(
        _url(account.host, account.account_odata_id),
        auth=auth,
        json={"Password": new_password},
        headers=headers,
        verify=False,
        timeout=timeout_s,
    )

    if resp.status_code in (200, 204):
        return

    # Try to extract Redfish MessageIds without echoing the password back.
    msg = f"HTTP {resp.status_code} {resp.reason}"
    try:
        body = _json_or_raise(resp)
        ext = (body.get("error") or {}).get("@Message.ExtendedInfo") or []
        msg_ids = [str(e["MessageId"]) for e in ext if isinstance(e, dict) and e.get("MessageId")]
        if msg_ids:
            msg += f" ({', '.join(msg_ids)})"
    except Exception:
        pass

    raise RedfishError(f"Failed to update account password: {msg}")


def list_accounts(
    host: str, admin_user: str, admin_password: str, *, timeout_s: int = 20
) -> list[dict[str, Any]]:
    """List Redfish accounts (non-secret fields only)."""
    auth = HTTPBasicAuth(admin_user, admin_password)
    return [
        {
            "odata_id": mid,
            "Id": det_j.get("Id"),
            "UserName": det_j.get("UserName"),
            "Enabled": det_j.get("Enabled"),
            "Locked": det_j.get("Locked"),
            "RoleId": det_j.get("RoleId"),
        }
        for mid, _resp, det_j in _iter_accounts(host, auth, timeout_s)
    ]


def create_account(
    host: str,
    *,
    admin_user: str,
    admin_password: str,
    username: str,
    password: str,
    role_id: str = "Administrator",
    enabled: bool = True,
    timeout_s: int = 20,
) -> None:
    """Create a Redfish account.

    Notes:
    - Some BMCs may ignore RoleId or require specific values. We surface Redfish
      MessageIds on failure to aid debugging.
    """
    auth = HTTPBasicAuth(admin_user, admin_password)
    accounts = _get_accounts_collection_url(host, auth, timeout_s)

    resp = requests.post(
        _url(host, accounts),
        auth=auth,
        json={"UserName": username, "Password": password, "RoleId": role_id, "Enabled": enabled},
        headers={"Content-Type": "application/json"},
        verify=False,
        timeout=timeout_s,
    )

    if resp.status_code in (200, 201, 204):
        return

    msg = f"HTTP {resp.status_code} {resp.reason}"
    try:
        body = _json_or_raise(resp)
        ext = (body.get("error") or {}).get("@Message.ExtendedInfo") or []
        msg_ids = [str(e["MessageId"]) for e in ext if isinstance(e, dict) and e.get("MessageId")]
        if msg_ids:
            msg += f" ({', '.join(msg_ids)})"
    except Exception:
        pass

    raise RedfishError(f"Failed to create account: {msg}")


def get_account_detail(
    host: str, admin_user: str, admin_password: str, username: str, *, timeout_s: int = 20
) -> dict[str, Any]:
    """Get full Redfish account detail for a username.

    Returns the raw Redfish JSON dict for the account, including AccountTypes,
    Locked, RoleId, Enabled, etc.  Raises RedfishError if not found.
    """
    auth = HTTPBasicAuth(admin_user, admin_password)
    for mid, det_resp, det_j in _iter_accounts(host, auth, timeout_s):
        uname = det_j.get("UserName") or det_j.get("Id")
        if uname == username:
            det_j["_odata_id"] = mid
            det_j["_etag"] = _extract_etag(det_resp, det_j)
            return det_j

    raise RedfishError(f"Account '{username}' not found on BMC {host}")


def patch_account(
    host: str,
    account_odata_id: str,
    admin_user: str,
    admin_password: str,
    payload: dict[str, Any],
    *,
    etag: str | None = None,
    timeout_s: int = 20,
) -> requests.Response:
    """PATCH arbitrary properties on a Redfish account. Returns the raw response."""
    auth = HTTPBasicAuth(admin_user, admin_password)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if etag:
        headers["If-Match"] = etag

    return requests.patch(
        _url(host, account_odata_id),
        auth=auth,
        json=payload,
        headers=headers,
        verify=False,
        timeout=timeout_s,
    )


def get_account_service_info(
    host: str, admin_user: str, admin_password: str, *, timeout_s: int = 20
) -> dict[str, Any]:
    """Read AccountService properties (lockout threshold, duration, etc.)."""
    auth = HTTPBasicAuth(admin_user, admin_password)
    root = requests.get(_url(host, "/redfish/v1"), auth=auth, verify=False, timeout=timeout_s)
    root.raise_for_status()
    root_j = _json_or_raise(root)
    acct_svc = (root_j.get("AccountService") or {}).get("@odata.id") or "/redfish/v1/AccountService"
    svc = requests.get(_url(host, acct_svc), auth=auth, verify=False, timeout=timeout_s)
    svc.raise_for_status()
    return _json_or_raise(svc)


def verify_login(host: str, user: str, password: str, *, timeout_s: int = 20) -> bool:
    """Best-effort login validation against a cheap endpoint."""
    auth = HTTPBasicAuth(user, password)
    resp = requests.get(
        _url(host, "/redfish/v1/Systems/1"), auth=auth, verify=False, timeout=timeout_s
    )
    return resp.status_code == 200
