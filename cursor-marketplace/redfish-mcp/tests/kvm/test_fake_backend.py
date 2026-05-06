"""Tests for FakeBackend."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.fake_backend import FakeBackend


class TestFakeBackendRecording:
    @pytest.mark.anyio
    async def test_open_emits_progress_and_returns_handle(self):
        events: list[ProgressEvent] = []

        async def capture(e: ProgressEvent) -> None:
            events.append(e)

        be = FakeBackend()
        handle = await be.open("10.0.0.1", "admin", "pw", capture)
        assert isinstance(handle, SessionHandle)
        assert handle.host == "10.0.0.1"
        assert handle.user == "admin"
        assert handle.backend == "fake"
        assert [e.stage for e in events] == [
            "authenticating",
            "fetching_jar",
            "starting_xvfb",
            "launching_java",
            "starting_vnc",
            "handshaking",
            "ready",
        ]
        assert be.calls == [("open", "10.0.0.1", "admin")]

    @pytest.mark.anyio
    async def test_screenshot_returns_stub_png(self):
        be = FakeBackend()
        handle = await _open(be)
        png = await be.screenshot(handle)
        assert png.startswith(b"\x89PNG")
        assert ("screenshot", handle.session_id) in be.calls

    @pytest.mark.anyio
    async def test_sendkeys_and_sendkey_recorded(self):
        be = FakeBackend()
        handle = await _open(be)
        await be.sendkeys(handle, "hello")
        await be.sendkey(handle, "Enter", ["Ctrl"])
        assert ("sendkeys", handle.session_id, "hello") in be.calls
        assert ("sendkey", handle.session_id, "Enter", ("Ctrl",)) in be.calls

    @pytest.mark.anyio
    async def test_close_is_idempotent(self):
        be = FakeBackend()
        handle = await _open(be)
        await be.close(handle)
        await be.close(handle)
        assert be.calls.count(("close", handle.session_id)) == 2

    @pytest.mark.anyio
    async def test_health_ok_by_default(self):
        be = FakeBackend()
        handle = await _open(be)
        assert await be.health(handle) == "ok"

    @pytest.mark.anyio
    async def test_fail_on_stage_raises(self):
        from redfish_mcp.kvm.exceptions import AuthFailedError

        be = FakeBackend(fail_on_stage="authenticating", fail_as=AuthFailedError)
        with pytest.raises(AuthFailedError):
            await be.open("h", "u", "p", _noop)


async def _open(be: FakeBackend) -> SessionHandle:
    return await be.open("h", "u", "p", _noop)


async def _noop(_e: ProgressEvent) -> None:
    return None
