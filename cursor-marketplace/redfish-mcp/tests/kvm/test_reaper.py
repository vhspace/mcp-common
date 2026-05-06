"""Tests for IdleReaper."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.backend import SessionHandle
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.reaper import IdleReaper


def _handle(sid: str) -> SessionHandle:
    return SessionHandle(session_id=sid, host="h", user="u", backend="fake", opened_at_ms=0)


class TestReaperSessionIdle:
    @pytest.mark.anyio
    async def test_reaps_stale_sessions(self):
        closed: list[str] = []
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        e = cache.put("h", "u", "fake", _handle("s1"))
        e.last_activity_ms = 0  # idle for 1000ms

        async def closer(entry):
            closed.append(entry.handle.session_id)
            cache.pop("h", "u", "fake")

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=10_000,
            close_session=closer,
            clock=lambda: now[0],
        )
        await reaper.tick()
        assert closed == ["s1"]

    @pytest.mark.anyio
    async def test_keeps_fresh_sessions(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        cache.put("h", "u", "fake", _handle("fresh"))  # activity=1000

        async def closer(_entry):
            raise AssertionError("should not close")

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=10_000,
            close_session=closer,
            clock=lambda: now[0],
        )
        await reaper.tick()


class TestReaperDaemonIdle:
    @pytest.mark.anyio
    async def test_no_exit_while_sessions_alive(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        cache.put("h", "u", "fake", _handle("s"))

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=60_000,
            daemon_idle_ms=1,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()
        assert reaper.should_exit() is False

    @pytest.mark.anyio
    async def test_exits_after_grace_period_with_no_sessions(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=200,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()  # starts the empty timer
        assert reaper.should_exit() is False
        now[0] = 1500  # 500ms later, past 200ms grace
        await reaper.tick()
        assert reaper.should_exit() is True

    @pytest.mark.anyio
    async def test_resets_grace_when_session_opens(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=200,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()  # empty, grace timer armed
        cache.put("h", "u", "fake", _handle("s"))
        now[0] = 1500
        await reaper.tick()  # non-empty → grace disarmed
        assert reaper.should_exit() is False


async def _dont_call(_entry) -> None:
    raise AssertionError("close_session should not be called in this test")


@pytest.fixture
def anyio_backend():
    return "asyncio"
