"""Tests for the RedfishClient class."""

import pytest
import requests
import responses

from redfish_mcp.redfish import RedfishClient, RedfishEndpoint, to_abs


class TestRedfishEndpoint:
    def test_system_url(self):
        ep = RedfishEndpoint(base_url="https://192.168.1.1", system_path="/redfish/v1/Systems/1")
        assert ep.system_url == "https://192.168.1.1/redfish/v1/Systems/1"

    def test_reset_url(self):
        ep = RedfishEndpoint(base_url="https://192.168.1.1", system_path="/redfish/v1/Systems/1")
        assert (
            ep.reset_url == "https://192.168.1.1/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
        )

    def test_frozen(self):
        ep = RedfishEndpoint(base_url="https://host", system_path="/redfish/v1/Systems/1")
        with pytest.raises(AttributeError):
            ep.base_url = "https://other"  # type: ignore[misc]


class TestRedfishClient:
    def _make_client(self, host: str = "192.168.1.1") -> RedfishClient:
        return RedfishClient(
            host=host, user="admin", password="pass", verify_tls=False, timeout_s=10
        )

    def test_base_url_construction(self):
        c = self._make_client("192.168.1.1")
        assert c.base_url == "https://192.168.1.1"

    def test_base_url_strips_trailing_slash(self):
        c = RedfishClient(
            host="192.168.1.1/", user="a", password="b", verify_tls=False, timeout_s=5
        )
        assert c.base_url == "https://192.168.1.1"

    def test_host_stored(self):
        c = self._make_client("10.0.0.1")
        assert c.host == "10.0.0.1"

    def test_context_manager(self):
        with self._make_client() as c:
            assert c.base_url == "https://192.168.1.1"
        # After exiting, session should be closed (no crash on second close)
        c.close()

    @responses.activate
    def test_get_json_success(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        c = self._make_client()
        data = c.get_json("https://192.168.1.1/redfish/v1/Systems")
        assert data["Members"][0]["@odata.id"] == "/redfish/v1/Systems/1"

    @responses.activate
    def test_get_json_raises_on_error(self):
        responses.add(responses.GET, "https://192.168.1.1/redfish/v1/Systems", status=401)
        c = self._make_client()
        with pytest.raises(requests.exceptions.HTTPError):
            c.get_json("https://192.168.1.1/redfish/v1/Systems")

    @responses.activate
    def test_get_json_maybe_success(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/test",
            json={"ok": True},
            status=200,
        )
        c = self._make_client()
        data, err = c.get_json_maybe("https://192.168.1.1/test")
        assert data == {"ok": True}
        assert err is None

    @responses.activate
    def test_get_json_maybe_http_error(self):
        responses.add(responses.GET, "https://192.168.1.1/test", body="Not Found", status=404)
        c = self._make_client()
        data, err = c.get_json_maybe("https://192.168.1.1/test")
        assert data is None
        assert "404" in err

    @responses.activate
    def test_get_json_maybe_non_json(self):
        responses.add(
            responses.GET, "https://192.168.1.1/test", body="<html>oops</html>", status=200
        )
        c = self._make_client()
        data, err = c.get_json_maybe("https://192.168.1.1/test")
        assert data is None
        assert "non-json" in err

    def test_get_json_maybe_connection_error(self):
        c = RedfishClient(host="192.0.2.1", user="a", password="b", verify_tls=False, timeout_s=1)
        data, err = c.get_json_maybe("https://192.0.2.1/redfish/v1")
        assert data is None
        assert err is not None

    @responses.activate
    def test_patch_json(self):
        responses.add(responses.PATCH, "https://192.168.1.1/test", json={"ok": True}, status=200)
        c = self._make_client()
        resp = c.patch_json("https://192.168.1.1/test", {"key": "value"})
        assert resp.status_code == 200

    @responses.activate
    def test_post_json(self):
        responses.add(responses.POST, "https://192.168.1.1/test", json={"ok": True}, status=201)
        c = self._make_client()
        resp = c.post_json("https://192.168.1.1/test", {"ResetType": "ForceRestart"})
        assert resp.status_code == 201

    @responses.activate
    def test_discover_system(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        c = self._make_client()
        ep = c.discover_system()
        assert ep.system_path == "/redfish/v1/Systems/1"
        assert ep.base_url == "https://192.168.1.1"

    @responses.activate
    def test_discover_system_no_members(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Systems",
            json={"Members": []},
            status=200,
        )
        c = self._make_client()
        with pytest.raises(RuntimeError, match="No Systems members"):
            c.discover_system()

    @responses.activate
    def test_discover_system_missing_odata_id(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Systems",
            json={"Members": [{"Name": "System"}]},
            status=200,
        )
        c = self._make_client()
        with pytest.raises(RuntimeError, match="Unexpected Systems Members"):
            c.discover_system()


class TestDiscoverManagers:
    """Tests for RedfishClient.discover_managers() and discover_dell_manager()."""

    def _make_client(self, host: str = "192.168.1.1") -> RedfishClient:
        return RedfishClient(
            host=host, user="admin", password="pass", verify_tls=False, timeout_s=10
        )

    @responses.activate
    def test_discover_managers_returns_members(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
                    {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
                ]
            },
            status=200,
        )
        c = self._make_client()
        members = c.discover_managers()
        assert len(members) == 2

    @responses.activate
    def test_discover_managers_empty(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={"Members": []},
            status=200,
        )
        c = self._make_client()
        assert c.discover_managers() == []

    @responses.activate
    def test_discover_dell_manager_single(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"}]},
            status=200,
        )
        c = self._make_client()
        assert c.discover_dell_manager() == "/redfish/v1/Managers/iDRAC.Embedded.1"

    @responses.activate
    def test_discover_dell_manager_dual(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"},
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
                ]
            },
            status=200,
        )
        c = self._make_client()
        assert c.discover_dell_manager() == "/redfish/v1/Managers/iDRAC.Embedded.1"

    @responses.activate
    def test_discover_dell_manager_only_hgx(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/HGX_BMC_0"}]},
            status=200,
        )
        c = self._make_client()
        assert c.discover_dell_manager() is None

    @responses.activate
    def test_discover_dell_manager_non_dell(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
            status=200,
        )
        c = self._make_client()
        assert c.discover_dell_manager() is None

    @responses.activate
    def test_discover_dell_manager_unreachable(self):
        responses.add(
            responses.GET,
            "https://192.168.1.1/redfish/v1/Managers",
            status=500,
        )
        c = self._make_client()
        assert c.discover_dell_manager() is None


class TestToAbs:
    def test_absolute_url_passthrough(self):
        assert to_abs("https://host", "https://other/path") == "https://other/path"

    def test_relative_path_with_slash(self):
        assert (
            to_abs("https://host", "/redfish/v1/Systems/1") == "https://host/redfish/v1/Systems/1"
        )

    def test_relative_path_without_slash(self):
        assert to_abs("https://host", "redfish/v1/Systems/1") == "https://host/redfish/v1/Systems/1"

    def test_http_passthrough(self):
        assert to_abs("https://host", "http://insecure/path") == "http://insecure/path"
