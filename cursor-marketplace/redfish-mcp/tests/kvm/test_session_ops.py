"""Tests for session_ops — timeout + stage-tracking wrapper."""

from __future__ import annotations

import asyncio

import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.session_ops import open_session, screenshot_session
from redfish_mcp.kvm.exceptions import AuthFailedError, StaleSessionError


class _FakeSlowBackend:
    """Fakes a backend.open that emits stages then hangs."""

    def __init__(self, hang_after_stage: str = "starting_vnc") -> None:
        self._hang_after = hang_after_stage

    async def open(self, host, user, password, progress):
        stages = (
            "authenticating",
            "fetching_jar",
            "starting_xvfb",
            "launching_java",
            "starting_vnc",
            "handshaking",
            "ready",
        )
        for stage in stages:
            await progress(ProgressEvent(stage=stage))
            if stage == self._hang_after:
                await asyncio.sleep(1000)
        return SessionHandle(session_id="s1", host=host, user=user, backend="fake", opened_at_ms=0)


class _FakeFastBackend:
    async def open(self, host, user, password, progress):
        for stage in ("authenticating", "ready"):
            await progress(ProgressEvent(stage=stage))
        return SessionHandle(session_id="s1", host=host, user=user, backend="fake", opened_at_ms=0)

    async def screenshot(self, session):
        return b"\x89PNG\r\n\x1a\nfake"


class _FakeFailingBackend:
    async def open(self, host, user, password, progress):
        await progress(ProgressEvent(stage="authenticating"))
        raise AuthFailedError("bad creds", stage="authenticating")


@pytest.mark.anyio
async def test_timeout_fires_with_last_seen_stage():
    publisher = ProgressPublisher()
    with pytest.raises(StaleSessionError) as exc_info:
        await open_session(
            backend=_FakeSlowBackend(hang_after_stage="starting_vnc"),
            progress=publisher,
            host="h",
            user="u",
            password="p",
            session_key="fake:h:u",
            timeout_s=0.2,
        )
    assert exc_info.value.stage == "starting_vnc"


@pytest.mark.anyio
async def test_happy_path_returns_handle_and_emits_stages():
    publisher = ProgressPublisher()
    q = publisher.subscribe("fake:h:u")
    handle = await open_session(
        backend=_FakeFastBackend(),
        progress=publisher,
        host="h",
        user="u",
        password="p",
        session_key="fake:h:u",
        timeout_s=2.0,
    )
    assert handle.session_id == "s1"
    events = []
    while not q.empty():
        events.append(await q.get())
    stages = [e.stage for e in events if e is not None]
    assert "authenticating" in stages
    assert "ready" in stages


@pytest.mark.anyio
async def test_backend_exception_propagates_not_converted_to_stale():
    publisher = ProgressPublisher()
    with pytest.raises(AuthFailedError):
        await open_session(
            backend=_FakeFailingBackend(),
            progress=publisher,
            host="h",
            user="u",
            password="p",
            session_key="fake:h:u",
            timeout_s=2.0,
        )


@pytest.mark.anyio
async def test_screenshot_timeout_raises_stale():
    class _SlowScreen:
        async def open(self, *a, **kw): ...
        async def screenshot(self, session):
            await asyncio.sleep(5)
            return b""

    fake_handle = SessionHandle(session_id="s", host="h", user="u", backend="fake", opened_at_ms=0)
    with pytest.raises(StaleSessionError):
        await screenshot_session(backend=_SlowScreen(), session=fake_handle, timeout_s=0.1)


def test_open_timeout_env_override(monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.daemon.session_ops import default_open_timeout_s

    monkeypatch.setenv("REDFISH_KVM_OPEN_TIMEOUT_S", "45.5")
    assert default_open_timeout_s() == 45.5

    monkeypatch.delenv("REDFISH_KVM_OPEN_TIMEOUT_S", raising=False)
    assert default_open_timeout_s() == 30.0

    monkeypatch.setenv("REDFISH_KVM_OPEN_TIMEOUT_S", "not-a-number")
    assert default_open_timeout_s() == 30.0


def test_screenshot_timeout_env_override(monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.daemon.session_ops import default_screenshot_timeout_s

    monkeypatch.setenv("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", "7.5")
    assert default_screenshot_timeout_s() == 7.5

    monkeypatch.delenv("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", raising=False)
    assert default_screenshot_timeout_s() == 15.0


@pytest.fixture
def anyio_backend():
    return "asyncio"
