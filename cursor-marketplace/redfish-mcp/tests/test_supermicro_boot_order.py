"""Tests for Supermicro OEM FixedBootOrder GET/PATCH."""

import pytest
import responses

from redfish_mcp.mcp_server import create_mcp_app
from redfish_mcp.redfish import RedfishClient
from redfish_mcp.supermicro_boot_order import (
    FIXED_BOOT_ORDER_PATH,
    _discover_system_id,
    get_fixed_boot_order,
    is_supermicro,
    set_fixed_boot_order,
)

MOCK_HOST = "192.168.1.100"
BASE = f"https://{MOCK_HOST}"
SYSTEMS_URL = f"{BASE}/redfish/v1/Systems"

SAMPLE_BOOT_ORDER = {
    "BootModeSelected": "UEFI",
    "UefiBootOrder#0": {
        "BootDeviceName": "UEFI Hard Disk:ubuntu",
        "BootDeviceType": "UEFI Hard Disk",
    },
    "UefiBootOrder#1": {
        "BootDeviceName": "UEFI PXE:UEFI: PXE IPv4 Intel(R) I350",
        "BootDeviceType": "UEFI PXE",
    },
}


def _make_client() -> RedfishClient:
    return RedfishClient(
        host=MOCK_HOST,
        user="admin",
        password="password",
        verify_tls=False,
        timeout_s=10,
    )


def _mock_systems_collection(system_id: str = "1") -> None:
    """Register a /redfish/v1/Systems response with the given member ID."""
    responses.add(
        responses.GET,
        SYSTEMS_URL,
        json={
            "Members": [{"@odata.id": f"/redfish/v1/Systems/{system_id}"}],
            "Members@odata.count": 1,
        },
        status=200,
    )


class TestDiscoverSystemId:
    @responses.activate
    def test_discovers_systems_1(self):
        _mock_systems_collection("1")
        path = _discover_system_id(_make_client())
        assert path == "/redfish/v1/Systems/1"

    @responses.activate
    def test_discovers_systems_self(self):
        _mock_systems_collection("Self")
        path = _discover_system_id(_make_client())
        assert path == "/redfish/v1/Systems/Self"


class TestIsSupermicro:
    @responses.activate
    def test_returns_true_for_supermicro_systems_1(self):
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        responses.add(responses.GET, oem_url, json={"Name": "Supermicro OEM"}, status=200)
        assert is_supermicro(_make_client()) is True

    @responses.activate
    def test_returns_true_for_supermicro_systems_self(self):
        _mock_systems_collection("Self")
        oem_url = f"{BASE}/redfish/v1/Systems/Self/Oem/Supermicro"
        responses.add(responses.GET, oem_url, json={"Name": "Supermicro OEM"}, status=200)
        assert is_supermicro(_make_client()) is True

    @responses.activate
    def test_returns_false_for_non_supermicro(self):
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        responses.add(responses.GET, oem_url, status=404, body="Not Found")
        assert is_supermicro(_make_client()) is False


class TestGetFixedBootOrder:
    @responses.activate
    def test_success_with_etag_systems_1(self):
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"abc123"'},
        )
        data, etag, err = get_fixed_boot_order(_make_client())
        assert err is None
        assert data == SAMPLE_BOOT_ORDER
        assert etag == '"abc123"'

    @responses.activate
    def test_success_with_systems_self(self):
        _mock_systems_collection("Self")
        fbo_url = f"{BASE}/redfish/v1/Systems/Self/Oem/Supermicro/FixedBootOrder"
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"xyz789"'},
        )
        data, etag, err = get_fixed_boot_order(_make_client())
        assert err is None
        assert data == SAMPLE_BOOT_ORDER
        assert etag == '"xyz789"'

    @responses.activate
    def test_success_without_etag(self):
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, fbo_url, json=SAMPLE_BOOT_ORDER, status=200)
        data, etag, err = get_fixed_boot_order(_make_client())
        assert err is None
        assert data is not None
        assert etag is None

    @responses.activate
    def test_404_returns_error(self):
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, fbo_url, status=404, body="Not Found")
        data, _etag, err = get_fixed_boot_order(_make_client())
        assert data is None
        assert err is not None
        assert "404" in err


class TestSetFixedBootOrder:
    @responses.activate
    def test_patch_with_if_match(self):
        _mock_systems_collection("1")
        # discover_system is called twice: once in get_fixed_boot_order, once in set
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"etag-value"'},
        )
        responses.add(responses.PATCH, fbo_url, status=202, json={})

        result = set_fixed_boot_order(_make_client(), {"BootModeSelected": "UEFI"})
        assert result["ok"] is True
        assert result["http_status"] == 202
        assert result["etag_sent"] == '"etag-value"'
        assert "reset" in result["note"].lower()

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        assert len(patch_calls) == 1
        assert patch_calls[0].request.headers.get("If-Match") == '"etag-value"'

    @responses.activate
    def test_patch_with_systems_self(self):
        _mock_systems_collection("Self")
        _mock_systems_collection("Self")
        fbo_url = f"{BASE}/redfish/v1/Systems/Self/Oem/Supermicro/FixedBootOrder"
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"self-etag"'},
        )
        responses.add(responses.PATCH, fbo_url, status=202, json={})

        result = set_fixed_boot_order(_make_client(), {"BootModeSelected": "UEFI"})
        assert result["ok"] is True
        assert result["http_status"] == 202

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        assert len(patch_calls) == 1
        assert "/Systems/Self/" in patch_calls[0].request.url

    @responses.activate
    def test_patch_without_etag(self):
        _mock_systems_collection("1")
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, fbo_url, json=SAMPLE_BOOT_ORDER, status=200)
        responses.add(responses.PATCH, fbo_url, status=200, json={})

        result = set_fixed_boot_order(_make_client(), {"BootModeSelected": "UEFI"})
        assert result["ok"] is True
        assert result["etag_sent"] is None

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        assert "If-Match" not in patch_calls[0].request.headers

    @responses.activate
    def test_patch_failure(self):
        _mock_systems_collection("1")
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"v1"'},
        )
        responses.add(responses.PATCH, fbo_url, status=412, body="Precondition Failed")

        result = set_fixed_boot_order(_make_client(), {"BootModeSelected": "UEFI"})
        assert result["ok"] is False
        assert result["http_status"] == 412

    @responses.activate
    def test_get_failure_prevents_patch(self):
        _mock_systems_collection("1")
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, fbo_url, status=500, body="Internal Server Error")

        result = set_fixed_boot_order(_make_client(), {"BootModeSelected": "UEFI"})
        assert result["ok"] is False
        assert "Failed to GET" in result["error"]


class TestLegacyConstant:
    def test_fixed_boot_order_path_unchanged(self):
        """FIXED_BOOT_ORDER_PATH kept for backward compat."""
        assert FIXED_BOOT_ORDER_PATH == "/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"


# ---------- MCP tool tests ----------


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


class TestMcpGetFixedBootOrder:
    @responses.activate
    @pytest.mark.anyio
    async def test_success(self, mcp_tools):
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, oem_url, json={"Name": "Supermicro"}, status=200)
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"v1"'},
        )

        result = await mcp_tools["redfish_get_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
        )
        assert result["ok"] is True
        assert result["fixed_boot_order"] == SAMPLE_BOOT_ORDER

    @responses.activate
    @pytest.mark.anyio
    async def test_non_supermicro(self, mcp_tools):
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        responses.add(responses.GET, oem_url, status=404, body="Not Found")

        result = await mcp_tools["redfish_get_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
        )
        assert result["ok"] is False
        assert "Supermicro" in result["error"]


class TestMcpSetFixedBootOrder:
    @responses.activate
    @pytest.mark.anyio
    async def test_success(self, mcp_tools):
        _mock_systems_collection("1")
        _mock_systems_collection("1")
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        fbo_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
        responses.add(responses.GET, oem_url, json={"Name": "Supermicro"}, status=200)
        responses.add(
            responses.GET,
            fbo_url,
            json=SAMPLE_BOOT_ORDER,
            status=200,
            headers={"ETag": '"v1"'},
        )
        responses.add(responses.PATCH, fbo_url, status=202, json={})

        result = await mcp_tools["redfish_set_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
            boot_order={"BootModeSelected": "UEFI"},
            allow_write=True,
            async_mode=False,
        )
        assert result["ok"] is True
        assert result["http_status"] == 202

    @pytest.mark.anyio
    async def test_requires_allow_write(self, mcp_tools):
        result = await mcp_tools["redfish_set_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
            boot_order={"BootModeSelected": "UEFI"},
            allow_write=False,
        )
        assert result["ok"] is False
        assert "allow_write" in result["error"]

    @pytest.mark.anyio
    async def test_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_set_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
            boot_order={"BootModeSelected": "UEFI"},
            allow_write=True,
            execution_mode="render_curl",
        )
        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert any("If-Match" in cmd for cmd in result["curl"])

    @responses.activate
    @pytest.mark.anyio
    async def test_non_supermicro(self, mcp_tools):
        _mock_systems_collection("1")
        oem_url = f"{BASE}/redfish/v1/Systems/1/Oem/Supermicro"
        responses.add(responses.GET, oem_url, status=404, body="Not Found")

        result = await mcp_tools["redfish_set_fixed_boot_order"](
            host=MOCK_HOST,
            user="admin",
            password="password",
            boot_order={"BootModeSelected": "UEFI"},
            allow_write=True,
            async_mode=False,
        )
        assert result["ok"] is False
        assert "Supermicro" in result["error"]
