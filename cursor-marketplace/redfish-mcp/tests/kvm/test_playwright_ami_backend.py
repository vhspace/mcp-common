"""Tests for PlaywrightAmiBackend (Gigabyte/AMI MegaRAC)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from redfish_mcp.kvm.backend import KVMBackend, ProgressEvent, SessionHandle
from redfish_mcp.kvm.backends.playwright_ami import (
    PlaywrightAmiBackend,
    _MODIFIER_MAP,
    _PLAYWRIGHT_KEY_MAP,
)
from redfish_mcp.kvm.exceptions import AuthFailedError, KVMError, StaleSessionError


@pytest.fixture
def backend() -> PlaywrightAmiBackend:
    return PlaywrightAmiBackend(headless=True)


@pytest.fixture
def progress() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def fake_handle() -> SessionHandle:
    return SessionHandle(
        session_id="pw-test123",
        host="10.0.0.1",
        user="admin",
        backend="playwright",
        opened_at_ms=1000,
    )


class TestProtocolCompliance:
    def test_satisfies_kvm_backend_protocol(self):
        b = PlaywrightAmiBackend()
        assert isinstance(b, KVMBackend)

    def test_has_all_required_methods(self):
        b = PlaywrightAmiBackend()
        for method in ("open", "screenshot", "sendkeys", "sendkey", "close", "health"):
            assert hasattr(b, method)
            assert callable(getattr(b, method))


class TestKeyMaps:
    def test_common_keys_mapped(self):
        assert _PLAYWRIGHT_KEY_MAP["enter"] == "Enter"
        assert _PLAYWRIGHT_KEY_MAP["tab"] == "Tab"
        assert _PLAYWRIGHT_KEY_MAP["escape"] == "Escape"
        assert _PLAYWRIGHT_KEY_MAP["f1"] == "F1"
        assert _PLAYWRIGHT_KEY_MAP["space"] == " "

    def test_modifier_map(self):
        assert _MODIFIER_MAP["ctrl"] == "Control"
        assert _MODIFIER_MAP["alt"] == "Alt"
        assert _MODIFIER_MAP["shift"] == "Shift"
        assert _MODIFIER_MAP["meta"] == "Meta"
        assert _MODIFIER_MAP["win"] == "Meta"


class TestHealthOnDeadSession:
    @pytest.mark.anyio
    async def test_returns_dead_for_unknown_session(self, backend, fake_handle):
        assert await backend.health(fake_handle) == "dead"


class TestCloseOnUnknownSession:
    @pytest.mark.anyio
    async def test_close_unknown_is_noop(self, backend, fake_handle):
        await backend.close(fake_handle)


class TestScreenshotOnUnknownSession:
    @pytest.mark.anyio
    async def test_raises_stale_for_unknown(self, backend, fake_handle):
        with pytest.raises(StaleSessionError):
            await backend.screenshot(fake_handle)


class TestSendkeysOnUnknownSession:
    @pytest.mark.anyio
    async def test_raises_stale_for_unknown(self, backend, fake_handle):
        with pytest.raises(StaleSessionError):
            await backend.sendkeys(fake_handle, "hello")


class TestSendkeyOnUnknownSession:
    @pytest.mark.anyio
    async def test_raises_stale_for_unknown(self, backend, fake_handle):
        with pytest.raises(StaleSessionError):
            await backend.sendkey(fake_handle, "enter")


class TestFormLoginErrors:
    @pytest.mark.anyio
    async def test_open_propagates_auth_error(self, backend, progress):
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch.object(
            backend, "_ensure_browser", return_value=(MagicMock(), mock_browser)
        ), patch.object(
            backend,
            "_form_login",
            side_effect=AuthFailedError("bad creds", stage="authenticating"),
        ):
            with pytest.raises(AuthFailedError, match="bad creds"):
                await backend.open("10.0.0.1", "admin", "bad", progress)

        stages = [call.args[0].stage for call in progress.call_args_list]
        assert "launching_browser" in stages
        assert "authenticating" in stages
        mock_context.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_open_propagates_kvm_error(self, backend, progress):
        mock_browser = MagicMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch.object(
            backend, "_ensure_browser", return_value=(MagicMock(), mock_browser)
        ), patch.object(
            backend,
            "_form_login",
            side_effect=KVMError("network error", stage="authenticating"),
        ):
            with pytest.raises(KVMError, match="network error"):
                await backend.open("10.0.0.1", "admin", "bad", progress)

        mock_context.close.assert_awaited_once()


class TestShutdown:
    @pytest.mark.anyio
    async def test_shutdown_cleans_up(self, backend):
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()
        backend._browser = mock_browser
        backend._playwright = mock_pw

        await backend.shutdown()

        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert backend._browser is None
        assert backend._playwright is None


class TestPreflightBackendAware:
    def test_java_deps_checked_by_default(self, monkeypatch):
        from redfish_mcp.kvm.daemon.preflight import check_runtime_deps

        monkeypatch.setattr(
            "redfish_mcp.kvm.daemon.preflight.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )
        check_runtime_deps("java")

    def test_playwright_deps_check(self, monkeypatch):
        from redfish_mcp.kvm.daemon.preflight import check_runtime_deps

        check_runtime_deps("playwright")


class TestScreenCaptureVendorMethods:
    def test_gigabyte_returns_ami(self):
        from redfish_mcp.screen_capture import vendor_methods

        assert vendor_methods("gigabyte") == ["ami"]

    def test_gigabyte_is_supported(self):
        from redfish_mcp.screen_capture import is_screenshot_supported

        assert is_screenshot_supported("gigabyte")
