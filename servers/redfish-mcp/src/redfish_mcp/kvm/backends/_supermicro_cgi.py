"""Supermicro BMC CGI HTTP client.

Two endpoints, used in sequence during JavaIkvmBackend.open():

    POST /cgi/login.cgi   with name=<user>&pwd=<password>
        → sets SID cookie; response body is HTML but we only care about the cookie.

    GET /cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk
        → returns JNLP XML with a rotated credential and JAR URL.

Newer Supermicro X13 firmware (HTML5-default) migrates login to Redfish:

    POST /redfish/v1/SessionService/Sessions with JSON body
        → returns X-Auth-Token header + SID cookie.

    GET/PATCH /redfish/v1/Managers/1/Oem/Supermicro/IKVM
        → reads or sets the 'Current interface' field (e.g. 'HTML 5' vs 'JAVA plug-in').

TLS verification defaults off because BMC certs are almost always self-signed
or expired. Enable via verify_tls=True when the environment has proper cert
distribution.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger("redfish_mcp.kvm.backends.supermicro_cgi")


class SupermicroCGIError(Exception):
    """Raised for unexpected responses from the Supermicro CGI endpoints."""


_LOGIN_PATH = "/cgi/login.cgi"
_JNLP_PATH = "/cgi/url_redirect.cgi"
_JNLP_PARAMS = {"url_name": "man_ikvm", "url_type": "jwsk"}

# Redfish SessionService path (newer Supermicro X13 firmware)
_SESSION_SERVICE_PATH = "/redfish/v1/SessionService/Sessions"

# Redfish iKVM OEM path for interface toggle
_IKVM_PATH = "/redfish/v1/Managers/1/Oem/Supermicro/IKVM"
_JAVA_INTERFACE_VALUE = "JAVA plug-in"


def _base_url(host: str) -> str:
    return f"https://{host}"


def _client(verify_tls: bool) -> httpx.Client:
    return httpx.Client(verify=verify_tls, timeout=httpx.Timeout(10.0, connect=5.0))


def login(*, host: str, user: str, password: str, verify_tls: bool = False) -> str:
    """POST credentials to the legacy CGI endpoint and return the SID cookie value.

    Raises SupermicroCGIError on HTTP error or missing SID cookie.

    This is the fallback path for older firmware. On newer firmware (X13+) that
    has migrated to Redfish SessionService, the CGI endpoint returns HTTP 400.
    Callers should try login_via_redfish() first and fall back to this only when
    the Redfish path raises SupermicroCGIError.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.post(
                f"{_base_url(host)}{_LOGIN_PATH}",
                data={"name": user, "pwd": password},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"login request failed: {exc}") from exc

    if resp.status_code == 400:
        logger.warning(
            "CGI login to %s returned HTTP 400 — firmware may have migrated to Redfish auth",
            host,
        )
        raise SupermicroCGIError(f"login returned HTTP {resp.status_code}: {resp.text[:200]}")

    if resp.status_code != 200:
        raise SupermicroCGIError(f"login returned HTTP {resp.status_code}: {resp.text[:200]}")

    sid = resp.cookies.get("SID")
    if not sid:
        raise SupermicroCGIError("login response missing SID cookie (bad credentials?)")
    return sid


def login_via_redfish(
    *, host: str, user: str, password: str, verify_tls: bool = False
) -> tuple[str, str]:
    """POST credentials to the Redfish SessionService and return (x_auth_token, sid).

    Used by newer Supermicro X13 firmware that has migrated from /cgi/login.cgi
    to POST /redfish/v1/SessionService/Sessions.

    Returns:
        (x_auth_token, sid) — both are non-empty strings.

    Raises:
        SupermicroCGIError if the HTTP status is not 201, or if either the
        X-Auth-Token header or SID cookie is absent from the response.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.post(
                f"{_base_url(host)}{_SESSION_SERVICE_PATH}",
                json={"UserName": user, "Password": password},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"Redfish session login request failed: {exc}") from exc

    if resp.status_code != 201:
        raise SupermicroCGIError(
            f"Redfish session login returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    x_auth_token = resp.headers.get("X-Auth-Token", "")
    if not x_auth_token:
        raise SupermicroCGIError("Redfish session login response missing X-Auth-Token header")

    # The SID cookie may appear in resp.cookies or must be parsed from Set-Cookie header
    # (httpx may not expose all cookies when there are duplicated semicolons in the value).
    sid = resp.cookies.get("SID", "")
    if not sid:
        # Fallback: parse raw Set-Cookie header for SID=<value>
        set_cookie = resp.headers.get("Set-Cookie", "")
        m = re.search(r"\bSID=([^;,\s]+)", set_cookie)
        if m:
            sid = m.group(1)

    if not sid:
        raise SupermicroCGIError("Redfish session login response missing SID cookie")

    return x_auth_token, sid


def get_current_interface(*, host: str, x_auth_token: str, verify_tls: bool = False) -> str:
    """GET /redfish/v1/Managers/1/Oem/Supermicro/IKVM and return 'Current interface' value.

    Typical values: "HTML 5", "JAVA plug-in".
    Raises SupermicroCGIError on non-200 or missing field.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.get(
                f"{_base_url(host)}{_IKVM_PATH}",
                headers={"X-Auth-Token": x_auth_token},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"iKVM interface GET request failed: {exc}") from exc

    if resp.status_code != 200:
        raise SupermicroCGIError(
            f"iKVM interface GET returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise SupermicroCGIError(f"iKVM interface GET response is not valid JSON: {exc}") from exc

    value = data.get("Current interface")
    if value is None:
        raise SupermicroCGIError(
            f"iKVM interface GET response missing 'Current interface' field: {data}"
        )

    return str(value)


def set_current_interface(
    *, host: str, x_auth_token: str, value: str, verify_tls: bool = False
) -> None:
    """PATCH /redfish/v1/Managers/1/Oem/Supermicro/IKVM with {"Current interface": value}.

    Raises SupermicroCGIError on non-200.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.patch(
                f"{_base_url(host)}{_IKVM_PATH}",
                json={"Current interface": value},
                headers={
                    "X-Auth-Token": x_auth_token,
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"iKVM interface PATCH request failed: {exc}") from exc

    if resp.status_code != 200:
        raise SupermicroCGIError(
            f"iKVM interface PATCH returned HTTP {resp.status_code}: {resp.text[:200]}"
        )


def fetch_jnlp(*, host: str, sid: str, verify_tls: bool = False) -> bytes:
    """Download the iKVM JNLP XML for the authenticated session.

    Returns raw bytes; caller parses via the _jnlp module.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.get(
                f"{_base_url(host)}{_JNLP_PATH}",
                params=_JNLP_PARAMS,
                cookies={"SID": sid},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"jnlp fetch failed: {exc}") from exc

    if resp.status_code != 200:
        raise SupermicroCGIError(f"jnlp fetch returned HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.content
