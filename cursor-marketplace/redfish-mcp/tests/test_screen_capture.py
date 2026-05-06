"""Tests for screen capture module and MCP tools."""

import base64
import hashlib

import pytest
import requests
import responses

from redfish_mcp.mcp_server import create_mcp_app
from redfish_mcp.redfish import RedfishClient, _pick_host_system
from redfish_mcp.screen_capture import (
    DellPrivilegeError,
    _sniff_mime,
    capture_screen_dell,
    capture_screen_redfish,
    detect_idrac_generation,
    detect_vendor,
    try_capture,
    vendor_from_manufacturer,
    vendor_from_model,
    vendor_methods,
)
from redfish_mcp.screenshot_cache import ScreenshotCache

MOCK_HOST = "192.168.1.100"
BASE = f"https://{MOCK_HOST}"
DUMP_ACTION = f"{BASE}/redfish/v1/Oem/Supermicro/DumpService/Actions/OemDumpService.Collect"

JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 1020
PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1016
BMP_HEADER = b"BM" + b"\x00" * 1022
WEBP_HEADER = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 1012


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


class TestSniffMime:
    def test_jpeg(self):
        assert _sniff_mime(b"\xff\xd8\xff\xe0rest", "") == "image/jpeg"

    def test_png(self):
        assert _sniff_mime(b"\x89PNG\r\n\x1a\nrest", "") == "image/png"

    def test_bmp(self):
        assert _sniff_mime(b"BMheader", "") == "image/bmp"

    def test_webp(self):
        assert _sniff_mime(b"RIFF\x00\x00\x00\x00WEBPrest", "") == "image/webp"

    def test_fallback_to_header(self):
        assert _sniff_mime(b"\x00\x00\x00\x00", "image/tiff") == "image/tiff"

    def test_fallback_to_header_strips_charset(self):
        assert _sniff_mime(b"\x00\x00\x00\x00", "image/png; charset=utf-8") == "image/png"

    def test_fallback_to_jpeg_default(self):
        assert _sniff_mime(b"\x00\x00\x00\x00", "application/octet-stream") == "image/jpeg"

    def test_empty_data(self):
        assert _sniff_mime(b"", "") == "image/jpeg"


class TestCaptureScreenRedfish:
    @responses.activate
    def test_success(self):
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=JPEG_HEADER,
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )

        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        data, mime = capture_screen_redfish(client)

        assert mime == "image/jpeg"
        assert data[:2] == b"\xff\xd8"
        assert len(data) == 1024

    @responses.activate
    def test_create_fails(self):
        responses.add(responses.POST, DUMP_ACTION, status=500)

        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        with pytest.raises(requests.exceptions.HTTPError):
            capture_screen_redfish(client)

    @responses.activate
    def test_download_too_small(self):
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(responses.POST, DUMP_ACTION, body=b"tiny", status=200)

        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        with pytest.raises(RuntimeError, match="unexpectedly small"):
            capture_screen_redfish(client)

    @responses.activate
    def test_png_detected(self):
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(responses.POST, DUMP_ACTION, body=PNG_HEADER, status=200)

        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        _data, mime = capture_screen_redfish(client)
        assert mime == "image/png"


class TestMcpCaptureScreenshot:
    @responses.activate
    @pytest.mark.anyio
    async def test_redfish_capture_returns_image_content(self, mcp_tools):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=JPEG_HEADER,
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )

        assert hasattr(result, "content")
        assert len(result.content) == 2

        img_content = result.content[0]
        assert img_content.type == "image"
        assert img_content.mimeType == "image/jpeg"
        decoded = base64.b64decode(img_content.data)
        assert decoded[:2] == b"\xff\xd8"

        import json

        meta = json.loads(result.content[1].text)
        assert meta["ok"] is True
        assert meta["method_used"] == "redfish"
        assert meta["size_bytes"] == 1024

    @responses.activate
    @pytest.mark.anyio
    async def test_auto_fallback_to_cgi(self, mcp_tools):
        responses.add(responses.POST, DUMP_ACTION, status=404)
        responses.add(
            responses.POST,
            f"{BASE}/cgi/login.cgi",
            headers={"Set-Cookie": "SID=abc123"},
            status=200,
        )
        responses.add(responses.GET, f"{BASE}/cgi/CapturePreview.cgi", status=200)
        responses.add(
            responses.GET,
            f"{BASE}/cgi/url_redirect.cgi",
            body=JPEG_HEADER,
            status=200,
        )
        responses.add(responses.GET, f"{BASE}/cgi/logout.cgi", status=200)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
        )

        assert hasattr(result, "content")
        import json

        meta = json.loads(result.content[1].text)
        assert meta["method_used"] == "cgi"

    @pytest.mark.anyio
    async def test_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            execution_mode="render_curl",
        )
        assert hasattr(result, "content")
        import json

        meta = json.loads(result.content[0].text)
        assert meta["ok"] is True
        assert meta["execution_mode"] == "render_curl"
        assert any("OemDumpService.Collect" in c for c in meta["curl"])

    @responses.activate
    @pytest.mark.anyio
    async def test_redfish_failure_returns_error(self, mcp_tools):
        responses.add(responses.POST, DUMP_ACTION, status=500)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )
        assert hasattr(result, "isError")
        assert result.isError is True

    @pytest.mark.anyio
    async def test_invalid_method(self, mcp_tools):
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="bogus",
            verify_tls=False,
            timeout_s=10,
        )
        assert hasattr(result, "isError")
        assert result.isError is True
        import json

        meta = json.loads(result.content[0].text)
        assert "Invalid method" in meta["error"]

    @responses.activate
    @pytest.mark.anyio
    async def test_auto_all_methods_fail_reports_errors(self, mcp_tools):
        responses.add(responses.POST, DUMP_ACTION, status=400)
        responses.add(responses.POST, f"{BASE}/cgi/login.cgi", status=400)
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            status=401,
        )
        responses.add(
            responses.POST,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService/Actions/DellLCService.ExportServerScreenShot",
            status=401,
        )
        responses.add(
            responses.POST,
            f"{BASE}/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService/Actions/DellLCService.ExportServerScreenShot",
            status=401,
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
        )
        assert result.isError is True
        import json

        meta = json.loads(result.content[0].text)
        assert "All screenshot methods failed" in meta["error"]
        assert "redfish:" in meta["error"]
        assert "cgi:" in meta["error"]


class TestMcpCaptureVideo:
    @responses.activate
    @pytest.mark.anyio
    async def test_download_video(self, mcp_tools, tmp_path):
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=b"\x00" * 1024,
            status=200,
            headers={"Content-Type": "video/avi"},
        )

        result = await mcp_tools["redfish_capture_video"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            capture_type="video",
            output_dir=str(tmp_path),
            verify_tls=False,
            timeout_s=10,
        )

        assert result["ok"] is True
        assert result["size_bytes"] == 1024
        assert (
            tmp_path / f"{MOCK_HOST.replace('.', '_')}_video.avi"
            == tmp_path / result["file_path"].split("/")[-1]
        )

    @responses.activate
    @pytest.mark.anyio
    async def test_empty_capture_returns_error(self, mcp_tools, tmp_path):
        responses.add(responses.POST, DUMP_ACTION, body=b"tiny", status=200)

        result = await mcp_tools["redfish_capture_video"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            capture_type="video",
            output_dir=str(tmp_path),
            verify_tls=False,
            timeout_s=10,
        )
        assert result["ok"] is False
        assert "empty" in result["error"].lower() or "small" in result["error"].lower()

    @pytest.mark.anyio
    async def test_invalid_capture_type(self, mcp_tools):
        result = await mcp_tools["redfish_capture_video"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            capture_type="bogus",
        )
        assert result["ok"] is False


class TestScreenshotCache:
    def test_new_host_is_always_changed(self):
        cache = ScreenshotCache()
        assert cache.has_changed("10.0.0.1", JPEG_HEADER) is True

    def test_same_bytes_is_not_changed(self):
        cache = ScreenshotCache()
        cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        assert cache.has_changed("10.0.0.1", JPEG_HEADER) is False

    def test_different_bytes_is_changed(self):
        cache = ScreenshotCache()
        cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        different = b"\xff\xd8\xff\xe0" + b"\x01" * 1020
        assert cache.has_changed("10.0.0.1", different) is True

    def test_host_key_is_case_insensitive(self):
        cache = ScreenshotCache()
        cache.store("HOST.EXAMPLE.COM", JPEG_HEADER, "image/jpeg", "redfish")
        assert cache.has_changed("host.example.com", JPEG_HEADER) is False

    def test_store_returns_entry_with_sha256(self):
        cache = ScreenshotCache()
        entry = cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        expected = hashlib.sha256(JPEG_HEADER).hexdigest()
        assert entry.sha256 == expected
        assert entry.host == "10.0.0.1"
        assert entry.mime_type == "image/jpeg"

    def test_get_returns_none_for_unknown(self):
        cache = ScreenshotCache()
        assert cache.get("unknown") is None

    def test_get_returns_cached_entry(self):
        cache = ScreenshotCache()
        cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        entry = cache.get("10.0.0.1")
        assert entry is not None
        assert entry.image_bytes == JPEG_HEADER

    def test_set_ocr_text(self):
        cache = ScreenshotCache()
        cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        cache.set_ocr_text("10.0.0.1", "BIOS Setup Utility")
        entry = cache.get("10.0.0.1")
        assert entry is not None
        assert entry.ocr_text == "BIOS Setup Utility"

    def test_eviction_at_max_entries(self):
        cache = ScreenshotCache(max_entries=2)
        cache.store("host-a", JPEG_HEADER, "image/jpeg", "redfish")
        cache.store("host-b", PNG_HEADER, "image/png", "redfish")
        cache.store("host-c", BMP_HEADER, "image/bmp", "redfish")
        assert cache.get("host-a") is None
        assert cache.get("host-b") is not None
        assert cache.get("host-c") is not None

    def test_invalidate(self):
        cache = ScreenshotCache()
        cache.store("10.0.0.1", JPEG_HEADER, "image/jpeg", "redfish")
        cache.invalidate("10.0.0.1")
        assert cache.get("10.0.0.1") is None


class TestMcpCaptureScreenshotCaching:
    """Test the cache / no_change / force / return_mode behaviors."""

    def _stub_redfish_capture(self, image_data=JPEG_HEADER):
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=image_data,
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )

    @responses.activate
    @pytest.mark.anyio
    async def test_first_capture_returns_image(self, mcp_tools):
        self._stub_redfish_capture()
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )
        assert not result.isError
        assert result.content[0].type == "image"
        import json

        meta = json.loads(result.content[1].text)
        assert meta["status"] == "changed"

    @responses.activate
    @pytest.mark.anyio
    async def test_second_capture_returns_image_when_cache_disabled(self, mcp_tools):
        self._stub_redfish_capture()
        await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )
        self._stub_redfish_capture()
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )
        assert not result.isError
        assert result.content[0].type == "image"
        import json

        meta = json.loads(result.content[1].text)
        assert meta["status"] == "changed"

    @responses.activate
    @pytest.mark.anyio
    async def test_force_always_returns_image(self, mcp_tools):
        self._stub_redfish_capture()
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            force=True,
        )
        assert not result.isError
        assert result.content[0].type == "image"
        import json

        meta = json.loads(result.content[1].text)
        assert meta["status"] == "changed"

    @responses.activate
    @pytest.mark.anyio
    async def test_text_only_mode(self, mcp_tools, monkeypatch):
        self._stub_redfish_capture()
        monkeypatch.setattr(
            "redfish_mcp.mcp_server.extract_text_from_screenshot",
            lambda *a, **kw: "American Megatrends BIOS v2.20",
        )
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="text_only",
        )
        assert not result.isError
        assert all(c.type == "text" for c in result.content)
        import json

        meta = json.loads(result.content[0].text)
        assert meta["return_mode"] == "text_only"
        assert "American Megatrends" in meta["ocr_text"]

    @responses.activate
    @pytest.mark.anyio
    async def test_both_mode(self, mcp_tools, monkeypatch):
        self._stub_redfish_capture()
        monkeypatch.setattr(
            "redfish_mcp.mcp_server.extract_text_from_screenshot",
            lambda *a, **kw: "POST screen text",
        )
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="both",
        )
        assert not result.isError
        assert result.content[0].type == "image"
        import json

        meta = json.loads(result.content[1].text)
        assert meta["return_mode"] == "both"
        assert "POST screen" in meta["ocr_text"]


class TestScreenshotPowerCheck:
    """Issue #27: pre-check PowerState before capture."""

    @responses.activate
    @pytest.mark.anyio
    async def test_powered_off_returns_clear_error(self, mcp_tools):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/1",
            json={"PowerState": "Off"},
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
        )
        assert result.isError is True
        import json

        meta = json.loads(result.content[0].text)
        assert "powered off" in meta["error"]
        assert meta["power_state"] == "Off"

    @responses.activate
    @pytest.mark.anyio
    async def test_powered_on_proceeds_normally(self, mcp_tools):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/1",
            json={"PowerState": "On"},
        )
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            force=True,
        )
        assert not result.isError

    @responses.activate
    @pytest.mark.anyio
    async def test_power_check_failure_still_proceeds(self, mcp_tools):
        """If PowerState check fails (e.g., auth error), don't block capture."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            status=401,
        )
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            force=True,
        )
        assert not result.isError


class TestScreenAnalysisModes:
    """Issue #28: LLM-powered screen analysis return modes."""

    @staticmethod
    def _stub_redfish_capture():
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)
        responses.add(responses.POST, DUMP_ACTION, body=JPEG_HEADER, status=200)

    @staticmethod
    def _stub_power_on():
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/1",
            json={"PowerState": "On"},
        )

    @responses.activate
    @pytest.mark.anyio
    async def test_summary_mode(self, mcp_tools, monkeypatch):
        self._stub_power_on()
        self._stub_redfish_capture()
        monkeypatch.setattr(
            "redfish_mcp.mcp_server.analyze_screenshot",
            lambda *a, **kw: {
                "summary": "BIOS splash screen",
                "screen_type": "bios_splash",
                "is_interactive": False,
                "needs_attention": False,
            },
        )
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="summary",
            force=True,
        )
        assert not result.isError
        assert all(c.type == "text" for c in result.content)
        import json

        meta = json.loads(result.content[0].text)
        assert meta["return_mode"] == "summary"
        assert meta["screen"]["screen_type"] == "bios_splash"

    @responses.activate
    @pytest.mark.anyio
    async def test_diagnosis_mode(self, mcp_tools, monkeypatch):
        self._stub_power_on()
        self._stub_redfish_capture()
        monkeypatch.setattr(
            "redfish_mcp.mcp_server.analyze_screenshot",
            lambda *a, **kw: {
                "summary": "Kernel panic",
                "screen_type": "kernel_panic",
                "is_interactive": False,
                "needs_attention": True,
                "boot_stage": "kernel_panic",
                "errors": ["VFS: Unable to mount root fs"],
                "diagnosis": "Root filesystem not found",
                "suggested_actions": ["Check drives"],
                "severity": "critical",
            },
        )
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="diagnosis",
            force=True,
        )
        assert not result.isError
        import json

        meta = json.loads(result.content[0].text)
        assert meta["return_mode"] == "diagnosis"
        assert meta["screen"]["severity"] == "critical"
        assert "VFS" in meta["screen"]["errors"][0]

    @responses.activate
    @pytest.mark.anyio
    async def test_analysis_uses_cache_key(self, mcp_tools, monkeypatch):
        """Verify that analysis results are stored in the screenshot cache."""
        self._stub_power_on()
        self._stub_redfish_capture()
        stored = {}

        def mock_analyze(*a, **kw):
            stored["called"] = True
            return {
                "summary": "test",
                "screen_type": "unknown",
                "is_interactive": False,
                "needs_attention": False,
            }

        monkeypatch.setattr("redfish_mcp.mcp_server.analyze_screenshot", mock_analyze)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="summary",
            force=True,
        )
        assert stored.get("called")
        import json

        meta = json.loads(result.content[0].text)
        assert meta["screen"]["summary"] == "test"

    @responses.activate
    @pytest.mark.anyio
    async def test_analysis_failure_returns_fallback(self, mcp_tools, monkeypatch):
        self._stub_power_on()
        self._stub_redfish_capture()
        monkeypatch.setattr(
            "redfish_mcp.mcp_server.analyze_screenshot",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API down")),
        )
        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="redfish",
            verify_tls=False,
            timeout_s=10,
            return_mode="summary",
            force=True,
        )
        assert not result.isError
        import json

        meta = json.loads(result.content[0].text)
        assert "API down" in meta["screen"]["_error"]


class TestScreenAnalysisModule:
    """Tests for screen_analysis.py module."""

    def test_strip_markdown_fences(self):
        from redfish_mcp.screen_analysis import _strip_markdown_fences

        assert _strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_invalid_mode_raises(self):
        from redfish_mcp.screen_analysis import analyze_screenshot

        with pytest.raises(ValueError, match="Invalid mode"):
            analyze_screenshot(b"fake", mode="bogus")


class TestScreenshotCacheAnalysis:
    """Tests for analysis caching in ScreenshotCache."""

    def test_set_and_get_analysis(self):
        cache = ScreenshotCache()
        cache.store("host1", JPEG_HEADER, "image/jpeg", "redfish")
        cache.set_analysis("host1", "summary", {"summary": "test"})
        assert cache.get_analysis("host1", "summary") == {"summary": "test"}
        assert cache.get_analysis("host1", "analysis") is None

    def test_get_analysis_no_entry(self):
        cache = ScreenshotCache()
        assert cache.get_analysis("unknown", "summary") is None


class TestDetectVendor:
    """Tests for detect_vendor() BMC vendor detection."""

    @responses.activate
    def test_supermicro_via_oem(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Supermicro": {}}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "supermicro"

    @responses.activate
    def test_dell_via_oem(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Dell": {}}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "dell"

    @responses.activate
    def test_hpe_via_oem(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Hpe": {}}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "hpe"

    @responses.activate
    def test_hpe_uppercase_via_oem(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"HPE": {}}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "hpe"

    @responses.activate
    def test_supermicro_via_product(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": "Supermicro BMC"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "supermicro"

    @responses.activate
    def test_dell_via_product_idrac(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": "Integrated Dell Remote Access Controller"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "dell"

    @responses.activate
    def test_unknown_vendor(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "unknown"

    @responses.activate
    def test_connection_error_returns_unknown(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            body=ConnectionError("refused"),
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "unknown"

    @responses.activate
    def test_gigabyte_via_gbt_oem(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={
                "Oem": {
                    "Ami": {"@odata.type": "#AMIServiceRoot.v1_0_0.AMIServiceRoot", "RtpVersion": "13.06"},
                    "Gbt": {"@odata.type": "#GBTServiceRoot.v1_0_0.GBTServiceRoot"},
                },
                "Product": "AMI Redfish Server",
                "Vendor": "AMI",
            },
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "gigabyte"

    @responses.activate
    def test_gigabyte_via_ami_oem_only(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Ami": {}}, "Product": ""},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "gigabyte"

    @responses.activate
    def test_gigabyte_via_ami_product(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": "AMI Redfish Server"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "gigabyte"

    @responses.activate
    def test_gigabyte_via_ami_vendor_field(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": "", "Vendor": "AMI"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "gigabyte"

    @responses.activate
    def test_gigabyte_via_giga_computing_vendor(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {}, "Product": "", "Vendor": "Giga Computing"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "gigabyte"

    @responses.activate
    def test_no_oem_key_returns_unknown(self):
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Product": "Something"},
        )
        client = RedfishClient(MOCK_HOST, "admin", "pass", verify_tls=False, timeout_s=10)
        assert detect_vendor(client) == "unknown"


class TestVendorMethods:
    """Tests for vendor_methods() method ordering."""

    def test_supermicro(self):
        assert vendor_methods("supermicro") == ["redfish", "cgi"]

    def test_dell(self):
        assert vendor_methods("dell") == ["dell"]

    def test_gigabyte(self):
        assert vendor_methods("gigabyte") == ["ami"]

    def test_hpe(self):
        assert vendor_methods("hpe") == []

    def test_unknown(self):
        assert vendor_methods("unknown") == ["redfish", "cgi", "dell"]


class TestVendorFromModel:
    """Tests for vendor_from_model() model-based vendor inference."""

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("PowerEdge XE9780", "dell"),
            ("PowerEdge XE9680", "dell"),
            ("PowerEdge R760", "dell"),
            ("poweredge xe9780", "dell"),
            ("SYS-421GE-TNRT2", "supermicro"),
            ("AS-4125GS-TNRT2", "supermicro"),
            ("ProLiant DL380 Gen10", "hpe"),
            ("Synergy 480 Gen10", "hpe"),
            ("", None),
            ("UnknownModel", None),
        ],
    )
    def test_model_to_vendor(self, model, expected):
        assert vendor_from_model(model) == expected


class TestVendorFromManufacturer:
    """Tests for vendor_from_manufacturer() manufacturer-based vendor inference."""

    @pytest.mark.parametrize(
        "manufacturer,expected",
        [
            ("Dell Inc.", "dell"),
            ("DELL", "dell"),
            ("Supermicro", "supermicro"),
            ("HPE", "hpe"),
            ("Hewlett Packard Enterprise", "hpe"),
            ("Giga Computing", "gigabyte"),
            ("Gigabyte", "gigabyte"),
            ("", None),
            ("Unknown Mfr", None),
        ],
    )
    def test_manufacturer_to_vendor(self, manufacturer, expected):
        assert vendor_from_manufacturer(manufacturer) == expected


class TestTryCaptureVendorHint:
    """Tests for try_capture() vendor_hint parameter — issue #102 regression fix."""

    @responses.activate
    def test_vendor_hint_dell_skips_supermicro(self):
        """When vendor_hint='dell', Supermicro methods are never tried."""
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )
        img, mime, method_used = try_capture(
            MOCK_HOST, "admin", "pass", method="auto", vendor_hint="dell"
        )
        assert method_used == "dell"
        assert img[:2] == b"\xff\xd8"
        called_urls = [c.request.url for c in responses.calls]
        assert not any("Supermicro" in u for u in called_urls)
        assert not any("cgi" in u for u in called_urls)

    @responses.activate
    def test_vendor_hint_supermicro_skips_dell(self):
        """When vendor_hint='supermicro', Dell methods are never tried."""
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=JPEG_HEADER,
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )
        img, mime, method_used = try_capture(
            MOCK_HOST, "admin", "pass", method="auto", vendor_hint="supermicro"
        )
        assert method_used == "redfish"
        called_urls = [c.request.url for c in responses.calls]
        assert not any("sysmgmt" in u for u in called_urls)
        assert not any("Dell" in u for u in called_urls)

    @responses.activate
    def test_vendor_hint_none_falls_to_detect_vendor(self):
        """Without vendor_hint, detect_vendor() is called normally."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Dell": {}}, "Product": ""},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Dell/Managers/iDRAC.Embedded.1",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )
        img, mime, method_used = try_capture(
            MOCK_HOST, "admin", "pass", method="auto", vendor_hint=None
        )
        assert method_used == "dell"

    @responses.activate
    def test_vendor_hint_unknown_still_detects(self):
        """vendor_hint='unknown' is treated same as None — detect_vendor() runs."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Dell": {}}, "Product": ""},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Dell/Managers/iDRAC.Embedded.1",
            status=404,
        )
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )
        img, mime, method_used = try_capture(
            MOCK_HOST, "admin", "pass", method="auto", vendor_hint="unknown"
        )
        assert method_used == "dell"


class TestMcpModelBasedVendorDetection:
    """Issue #102: MCP tool should derive vendor from system model when detect_vendor fails."""

    @responses.activate
    @pytest.mark.anyio
    async def test_dell_xe9780_model_skips_supermicro(self, mcp_tools):
        """PowerEdge XE9780 model triggers Dell-only methods — no Supermicro attempts."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/System.Embedded.1",
            json={
                "PowerState": "On",
                "Model": "PowerEdge XE9780",
                "Manufacturer": "Dell Inc.",
            },
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1",
            json={"FirmwareVersion": "7.10.50.00"},
        )
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
            force=True,
        )

        assert not result.isError
        import json

        meta = json.loads(result.content[1].text)
        assert meta["method_used"] == "dell"
        called_urls = [c.request.url for c in responses.calls]
        assert not any("Supermicro" in u for u in called_urls)
        assert not any("cgi" in u.lower() for u in called_urls)

    @responses.activate
    @pytest.mark.anyio
    async def test_dell_model_with_failed_redfish_root(self, mcp_tools):
        """Model-based detection works even when /redfish/v1 root is unreachable."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]},
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Systems/System.Embedded.1",
            json={
                "PowerState": "On",
                "Model": "PowerEdge XE9680",
                "Manufacturer": "Dell Inc.",
            },
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1",
            status=401,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell",
            status=401,
        )
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1/Dell/Managers/iDRAC.Embedded.1",
            status=401,
        )
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
            force=True,
        )

        assert not result.isError
        import json

        meta = json.loads(result.content[1].text)
        assert meta["method_used"] == "dell"


class TestMcpCaptureScreenshotVendorDetection:
    """Tests that auto mode uses vendor detection to optimize method ordering."""

    @responses.activate
    @pytest.mark.anyio
    async def test_auto_supermicro_skips_dell(self, mcp_tools):
        """For Supermicro, auto should try redfish+cgi only, not dell."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Supermicro": {}}, "Product": ""},
        )
        responses.add(responses.POST, DUMP_ACTION, json={"Success": {}}, status=200)
        responses.add(
            responses.POST,
            DUMP_ACTION,
            body=JPEG_HEADER,
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
        )

        assert not result.isError
        import json

        meta = json.loads(result.content[1].text)
        assert meta["method_used"] == "redfish"

    @responses.activate
    @pytest.mark.anyio
    async def test_auto_dell_skips_supermicro(self, mcp_tools):
        """For Dell, auto should only try dell method."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Dell": {}}, "Product": ""},
        )
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
        )

        assert not result.isError
        import json

        meta = json.loads(result.content[1].text)
        assert meta["method_used"] == "dell"

    @responses.activate
    @pytest.mark.anyio
    async def test_auto_all_fail_includes_vendor_info(self, mcp_tools):
        """When all methods fail, error should include detected vendor."""
        responses.add(
            responses.GET,
            f"{BASE}/redfish/v1",
            json={"Oem": {"Supermicro": {}}, "Product": ""},
        )
        responses.add(responses.POST, DUMP_ACTION, status=500)
        responses.add(responses.POST, f"{BASE}/cgi/login.cgi", status=500)

        result = await mcp_tools["redfish_capture_screenshot"](
            host=MOCK_HOST,
            user="admin",
            password="pass",
            method="auto",
            verify_tls=False,
            timeout_s=10,
        )

        assert result.isError is True
        import json

        meta = json.loads(result.content[0].text)
        assert "supermicro" in meta["error"].lower()
        assert meta.get("vendor") == "supermicro"
        assert "dell" not in [m for m in meta.get("methods_tried", [])]


IDRAC_MANAGER_URL = f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1"
IDRAC10_OEM_BASE = f"{BASE}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell"
IDRAC9_OEM_BASE = f"{BASE}/redfish/v1/Dell/Managers/iDRAC.Embedded.1"
IDRAC10_SCREENSHOT = (
    f"{IDRAC10_OEM_BASE}/DellLCService/Actions/DellLCService.ExportServerScreenShot"
)
IDRAC9_SCREENSHOT = f"{IDRAC9_OEM_BASE}/DellLCService/Actions/DellLCService.ExportServerScreenShot"


class TestDetectIdracGeneration:
    """Tests for detect_idrac_generation()."""

    @responses.activate
    def test_idrac10_by_firmware_version(self):
        responses.add(
            responses.GET,
            IDRAC_MANAGER_URL,
            json={"FirmwareVersion": "7.10.50.00"},
        )
        client = RedfishClient(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
        )
        assert detect_idrac_generation(client) == "idrac10"

    @responses.activate
    def test_idrac9_by_firmware_version(self):
        responses.add(
            responses.GET,
            IDRAC_MANAGER_URL,
            json={"FirmwareVersion": "6.10.80.00"},
        )
        client = RedfishClient(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
        )
        assert detect_idrac_generation(client) == "idrac9"

    @responses.activate
    def test_fallback_to_path_probe_idrac10(self):
        """Manager returns no FirmwareVersion; iDRAC10 OEM path exists."""
        responses.add(
            responses.GET,
            IDRAC_MANAGER_URL,
            json={},
        )
        responses.add(
            responses.GET,
            IDRAC10_OEM_BASE,
            json={"@odata.id": "/some/path"},
        )
        client = RedfishClient(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
        )
        assert detect_idrac_generation(client) == "idrac10"

    @responses.activate
    def test_fallback_to_path_probe_idrac9(self):
        """Manager returns no FirmwareVersion; only iDRAC9 OEM path exists."""
        responses.add(
            responses.GET,
            IDRAC_MANAGER_URL,
            json={},
        )
        responses.add(
            responses.GET,
            IDRAC10_OEM_BASE,
            status=404,
        )
        responses.add(
            responses.GET,
            IDRAC9_OEM_BASE,
            json={"@odata.id": "/some/path"},
        )
        client = RedfishClient(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
        )
        assert detect_idrac_generation(client) == "idrac9"

    @responses.activate
    def test_manager_unreachable_returns_unknown(self):
        responses.add(
            responses.GET,
            IDRAC_MANAGER_URL,
            status=404,
        )
        responses.add(
            responses.GET,
            IDRAC10_OEM_BASE,
            status=404,
        )
        responses.add(
            responses.GET,
            IDRAC9_OEM_BASE,
            status=404,
        )
        client = RedfishClient(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
        )
        assert detect_idrac_generation(client) == "unknown"


class TestCaptureScreenDellIdrac10:
    """Tests for iDRAC10 OEM path fallback in capture_screen_dell()."""

    @responses.activate
    def test_idrac10_oem_path_success(self):
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            status=404,
        )
        responses.add(
            responses.POST,
            IDRAC10_SCREENSHOT,
            body=JPEG_HEADER,
            status=200,
        )
        data, mime = capture_screen_dell(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
            idrac_generation="idrac10",
        )
        assert data[:2] == b"\xff\xd8"
        assert mime == "image/jpeg"

    @responses.activate
    def test_idrac9_oem_path_fallback(self):
        """iDRAC10 path returns 404, iDRAC9 path works."""
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            status=404,
        )
        responses.add(
            responses.POST,
            IDRAC10_SCREENSHOT,
            status=404,
        )
        responses.add(
            responses.POST,
            IDRAC9_SCREENSHOT,
            body=JPEG_HEADER,
            status=200,
        )
        data, _mime = capture_screen_dell(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
            idrac_generation="unknown",
        )
        assert data[:2] == b"\xff\xd8"

    @responses.activate
    def test_idrac9_generation_tries_idrac9_first(self):
        """When generation=idrac9, tries the iDRAC9 path first."""
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            status=404,
        )
        responses.add(
            responses.POST,
            IDRAC9_SCREENSHOT,
            body=JPEG_HEADER,
            status=200,
        )
        data, _mime = capture_screen_dell(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
            idrac_generation="idrac9",
        )
        assert data[:2] == b"\xff\xd8"

    @responses.activate
    def test_lc081_privilege_error(self):
        """LC081 privilege denial raises DellPrivilegeError."""
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            status=404,
        )
        lc081_body = (
            '{"error":{"@Message.ExtendedInfo":[{'
            '"Message":"LC081: Unable to perform action because '
            "VirtualConsole.1.AccessPrivilege is set to "
            'Deny Access."}]}}'
        )
        responses.add(
            responses.POST,
            IDRAC10_SCREENSHOT,
            body=lc081_body,
            status=400,
        )
        with pytest.raises(DellPrivilegeError, match="VirtualConsole"):
            capture_screen_dell(
                MOCK_HOST,
                "admin",
                "pass",
                verify_tls=False,
                timeout_s=10,
                idrac_generation="idrac10",
            )

    @responses.activate
    def test_sysmgmt_preview_still_preferred(self):
        """sysmgmt preview endpoint is still tried first."""
        responses.add(
            responses.GET,
            f"{BASE}/sysmgmt/2015/server/preview",
            body=JPEG_HEADER,
            status=200,
        )
        data, _mime = capture_screen_dell(
            MOCK_HOST,
            "admin",
            "pass",
            verify_tls=False,
            timeout_s=10,
            idrac_generation="idrac10",
        )
        assert data[:2] == b"\xff\xd8"


class TestPickHostSystem:
    """Tests for _pick_host_system() multi-member routing."""

    def test_single_member(self):
        members = [
            {"@odata.id": "/redfish/v1/Systems/1"},
        ]
        chosen = _pick_host_system(members)
        assert chosen["@odata.id"] == "/redfish/v1/Systems/1"

    def test_prefers_system_embedded_1(self):
        members = [
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
            {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
        ]
        chosen = _pick_host_system(members)
        assert chosen["@odata.id"].endswith("System.Embedded.1")

    def test_avoids_hgx_when_no_embedded(self):
        members = [
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
            {"@odata.id": "/redfish/v1/Systems/HostServer"},
        ]
        chosen = _pick_host_system(members)
        assert chosen["@odata.id"].endswith("HostServer")

    def test_all_hgx_falls_back_to_first(self):
        members = [
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_1"},
        ]
        chosen = _pick_host_system(members)
        assert chosen["@odata.id"].endswith("HGX_Baseboard_0")

    def test_b300_dual_system_layout(self):
        """Realistic B300 layout: HGX first, Dell host second."""
        members = [
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
            {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
        ]
        chosen = _pick_host_system(members)
        assert chosen["@odata.id"] == "/redfish/v1/Systems/System.Embedded.1"

    def test_single_invalid_member_raises(self):
        with pytest.raises(RuntimeError):
            _pick_host_system([{"no_odata": True}])
