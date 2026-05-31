"""Tests for MaasRestClient HTTP methods, OAuth handling, and timeout behavior."""

import pytest
import responses

from maas_mcp.maas_client import MaasRestClient, is_maas_http_error


def _make_client(**kwargs) -> MaasRestClient:
    defaults = {
        "url": "http://maas.example.com:5240/MAAS",
        "api_key": "consumer:token:secret",
        "verify_ssl": False,
        "timeout_seconds": 5.0,
    }
    defaults.update(kwargs)
    return MaasRestClient(**defaults)


class TestOAuthKeyParsing:
    def test_valid_key(self):
        client = _make_client(api_key="key:tok:sec")
        assert client.base_url.endswith("/MAAS")

    def test_invalid_key_too_few_parts(self):
        with pytest.raises(ValueError, match="consumer_key:consumer_token:secret"):
            _make_client(api_key="only-one-part")

    def test_invalid_key_two_parts(self):
        with pytest.raises(ValueError, match="consumer_key:consumer_token:secret"):
            _make_client(api_key="key:token")

    def test_key_with_colons_in_secret(self):
        client = _make_client(api_key="key:tok:sec:with:colons")
        assert client.base_url.endswith("/MAAS")


class TestURLNormalization:
    def test_url_without_maas_suffix(self):
        client = _make_client(url="http://maas.example.com:5240")
        assert client.base_url == "http://maas.example.com:5240/MAAS"

    def test_url_with_maas_suffix(self):
        client = _make_client(url="http://maas.example.com:5240/MAAS")
        assert client.base_url == "http://maas.example.com:5240/MAAS"

    def test_url_trailing_slash_stripped(self):
        client = _make_client(url="http://maas.example.com:5240/MAAS/")
        assert client.base_url == "http://maas.example.com:5240/MAAS"

    def test_api_url_trailing_slash(self):
        client = _make_client()
        url = client._get_api_url("machines")
        assert url.endswith("/api/2.0/machines/")

    def test_api_url_nested_endpoint(self):
        client = _make_client()
        url = client._get_api_url("machines/abc123/interfaces")
        assert url.endswith("/api/2.0/machines/abc123/interfaces/")


class TestHTTPMethods:
    @responses.activate
    def test_get_returns_json(self):
        client = _make_client()
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/version/",
            json={"version": "3.4.0"},
            status=200,
        )
        result = client.get("version")
        assert result == {"version": "3.4.0"}

    @responses.activate
    def test_get_raises_on_404(self):
        client = _make_client()
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/nonexistent/",
            json={"error": "Not found"},
            status=404,
        )
        with pytest.raises(RuntimeError, match="404"):
            client.get("machines/nonexistent")

    @responses.activate
    def test_post_returns_json(self):
        client = _make_client()
        responses.post(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/abc/",
            json={"status": "Commissioning"},
            status=200,
        )
        result = client.post("machines/abc", data={"op": "commission"})
        assert result == {"status": "Commissioning"}

    @responses.activate
    def test_post_empty_response(self):
        client = _make_client()
        responses.post(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/abc/",
            body="",
            status=200,
        )
        result = client.post("machines/abc", data={})
        assert result is None

    @responses.activate
    def test_put_returns_json(self):
        client = _make_client()
        responses.put(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/abc/",
            json={"ok": True},
            status=200,
        )
        result = client.put("machines/abc", data={"power_parameters_power_user": "admin"})
        assert result == {"ok": True}

    @responses.activate
    def test_delete_succeeds(self):
        client = _make_client()
        responses.delete(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/abc/",
            status=204,
        )
        client.delete("machines/abc")

    @responses.activate
    def test_delete_raises_on_error(self):
        client = _make_client()
        responses.delete(
            "http://maas.example.com:5240/MAAS/api/2.0/machines/abc/",
            json={"error": "Forbidden"},
            status=403,
        )
        with pytest.raises(RuntimeError, match="403"):
            client.delete("machines/abc")


class TestTimeout:
    @responses.activate
    def test_timeout_is_passed_to_requests(self):
        """Verify timeout is actually sent to the requests library."""
        client = _make_client(timeout_seconds=7.0)
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/version/",
            json={"version": "3.4.0"},
            status=200,
        )
        client.get("version")
        assert responses.calls[0].request.req_kwargs.get("timeout") == 7.0

    @responses.activate
    def test_connection_error_raises_runtime_error(self):
        """Network errors are wrapped in RuntimeError."""
        import requests as req_lib

        client = _make_client()
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/version/",
            body=req_lib.exceptions.ConnectionError("Connection refused"),
        )
        with pytest.raises(RuntimeError, match="failed"):
            client.get("version")


class TestGetVersion:
    @responses.activate
    def test_version_cached(self):
        client = _make_client()
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/version/",
            json={"version": "3.4.0"},
            status=200,
        )
        v1 = client.get_version()
        v2 = client.get_version()
        assert v1 == "3.4.0"
        assert v2 == "3.4.0"
        assert len(responses.calls) == 1

    @responses.activate
    def test_version_defaults_on_error(self):
        client = _make_client()
        responses.get(
            "http://maas.example.com:5240/MAAS/api/2.0/version/",
            json={"error": "nope"},
            status=500,
        )
        v = client.get_version()
        assert v == "2.0.0"


class TestIsMaasHttpError:
    def test_404_true(self) -> None:
        exc = RuntimeError(
            'MAAS GET http://maas/MAAS/api/2.0/machines/x/ failed: 404 {"error":"Not found"}'
        )
        assert is_maas_http_error(exc, 404) is True

    def test_500_false_for_404_check(self) -> None:
        exc = RuntimeError("MAAS GET http://maas/MAAS/api/2.0/version/ failed: 500 oops")
        assert is_maas_http_error(exc, 404) is False

    def test_non_maas_runtime_error_false(self) -> None:
        assert is_maas_http_error(RuntimeError("something else"), 404) is False

    def test_wrong_type_false(self) -> None:
        assert is_maas_http_error(ValueError("MAAS GET x failed: 404 "), 404) is False


class TestClose:
    def test_close_does_not_raise(self):
        client = _make_client()
        client.close()
