"""Tests for SessionCache."""

from __future__ import annotations

from redfish_mcp.kvm.backend import SessionHandle
from redfish_mcp.kvm.daemon.cache import CacheEntry, SessionCache


def _handle(sid: str = "s1") -> SessionHandle:
    return SessionHandle(session_id=sid, host="h", user="u", backend="fake", opened_at_ms=0)


class TestSessionCache:
    def test_put_then_get(self):
        c = SessionCache(clock=lambda: 100)
        entry = c.put("h", "u", "fake", _handle())
        got = c.get("h", "u", "fake")
        assert got is entry
        assert entry.last_activity_ms == 100

    def test_get_updates_last_activity(self):
        now = [100]
        c = SessionCache(clock=lambda: now[0])
        c.put("h", "u", "fake", _handle())
        now[0] = 250
        e = c.get("h", "u", "fake")
        assert e is not None
        assert e.last_activity_ms == 250

    def test_miss_returns_none(self):
        c = SessionCache(clock=lambda: 0)
        assert c.get("nope", "u", "fake") is None

    def test_pop_removes_entry(self):
        c = SessionCache(clock=lambda: 0)
        c.put("h", "u", "fake", _handle())
        e = c.pop("h", "u", "fake")
        assert e is not None
        assert c.get("h", "u", "fake") is None

    def test_snapshot_is_a_copy(self):
        c = SessionCache(clock=lambda: 0)
        c.put("a", "u", "fake", _handle("sa"))
        c.put("b", "u", "fake", _handle("sb"))
        snap = c.snapshot()
        assert len(snap) == 2
        snap.clear()
        assert len(c.snapshot()) == 2

    def test_idle_entries_returns_stale(self):
        now = [100]
        c = SessionCache(clock=lambda: now[0])
        c.put("a", "u", "fake", _handle("sa"))
        now[0] = 500
        c.put("b", "u", "fake", _handle("sb"))
        # a is 400ms idle, b is 0ms idle
        stale = c.idle_entries(threshold_ms=300)
        assert len(stale) == 1
        assert stale[0].handle.session_id == "sa"

    def test_cacheentry_touch(self):
        e = CacheEntry(handle=_handle(), last_activity_ms=0)
        e.touch(1234)
        assert e.last_activity_ms == 1234
