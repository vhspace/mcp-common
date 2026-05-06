"""Tests for the Supermicro CGI client."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from redfish_mcp.kvm.backends._supermicro_cgi import (
    SupermicroCGIError,
    fetch_jnlp,
    get_current_interface,
    login,
    login_via_redfish,
    set_current_interface,
)


class TestLogin:
    def test_login_posts_credentials_and_returns_sid(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            headers={"Set-Cookie": "SID=abc123; Path=/; HttpOnly"},
            text="<html><body>OK</body></html>",
            status_code=200,
        )
        sid = login(host="10.0.0.1", user="ADMIN", password="pw", verify_tls=False)
        assert sid == "abc123"

        request = httpx_mock.get_request()
        assert request is not None
        body = request.content.decode()
        assert "name=ADMIN" in body
        assert "pwd=pw" in body

    def test_login_without_sid_cookie_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            text="<html><body>Invalid username or password</body></html>",
            status_code=200,
        )
        with pytest.raises(SupermicroCGIError) as exc_info:
            login(host="10.0.0.1", user="ADMIN", password="bad", verify_tls=False)
        assert "SID" in str(exc_info.value)

    def test_login_http_error_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            status_code=500,
            text="internal error",
        )
        with pytest.raises(SupermicroCGIError):
            login(host="10.0.0.1", user="ADMIN", password="pw", verify_tls=False)


class TestFetchJnlp:
    def test_fetch_jnlp_returns_xml_bytes(self, httpx_mock: HTTPXMock):
        jnlp_body = b"<?xml version='1.0'?><jnlp></jnlp>"
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk",
            content=jnlp_body,
            status_code=200,
        )
        result = fetch_jnlp(host="10.0.0.1", sid="abc123", verify_tls=False)
        assert result == jnlp_body

        request = httpx_mock.get_request()
        assert request is not None
        cookie_header = request.headers.get("cookie", "")
        assert "SID=abc123" in cookie_header

    def test_fetch_jnlp_404_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk",
            status_code=404,
        )
        with pytest.raises(SupermicroCGIError):
            fetch_jnlp(host="10.0.0.1", sid="abc123", verify_tls=False)


class TestLoginViaRedfish:
    def test_login_via_redfish_returns_token_and_sid(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/redfish/v1/SessionService/Sessions",
            status_code=201,
            headers=[
                ("X-Auth-Token", "tok123"),
                ("Set-Cookie", "SID=sid456; path=/ ;;Secure; HttpOnly"),
                ("Location", "/redfish/v1/SessionService/Sessions/1"),
            ],
            json={"Id": "1", "UserName": "taiuser"},
        )
        token, sid = login_via_redfish(host="10.0.0.1", user="u", password="p", verify_tls=False)
        assert token == "tok123"
        assert sid == "sid456"

    def test_login_via_redfish_missing_token_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/redfish/v1/SessionService/Sessions",
            status_code=201,
            headers=[("Set-Cookie", "SID=s; path=/")],
            json={},
        )
        with pytest.raises(SupermicroCGIError):
            login_via_redfish(host="10.0.0.1", user="u", password="p", verify_tls=False)

    def test_login_via_redfish_4xx_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/redfish/v1/SessionService/Sessions",
            status_code=401,
        )
        with pytest.raises(SupermicroCGIError):
            login_via_redfish(host="10.0.0.1", user="u", password="p", verify_tls=False)


class TestInterfaceToggle:
    def test_get_current_interface_returns_value(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/redfish/v1/Managers/1/Oem/Supermicro/IKVM",
            json={"Current interface": "HTML 5"},
        )
        assert (
            get_current_interface(host="10.0.0.1", x_auth_token="t", verify_tls=False) == "HTML 5"
        )

    def test_set_current_interface_patches(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="PATCH",
            url="https://10.0.0.1/redfish/v1/Managers/1/Oem/Supermicro/IKVM",
            json={"Success": {"code": "Base.1.10.3.Success"}},
        )
        set_current_interface(
            host="10.0.0.1", x_auth_token="t", value="JAVA plug-in", verify_tls=False
        )
        req = httpx_mock.get_request()
        assert req is not None
        assert b"JAVA plug-in" in req.content
        assert req.headers.get("X-Auth-Token") == "t"
