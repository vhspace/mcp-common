"""Tests for observation logging helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mcp.agent_state_store import AgentStateStore
from redfish_mcp.kvm.daemon.observations import ObservationKind, ObservationLogger


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "state.sqlite3"
    s = AgentStateStore(db_path=db)
    yield s
    s.close()


class TestObservationLogger:
    def test_session_opened_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.session_opened(host="h1", user="u", backend="fake", session_id="s1")
        rows = store.list_observations(host_key="h1")
        matches = [r for r in rows if r["kind"] == ObservationKind.SESSION_OPENED.value]
        assert len(matches) == 1
        row = matches[0]
        assert "h1" in row["summary"]
        assert row["details"]["backend"] == "fake"
        assert row["details"]["session_id"] == "s1"

    def test_session_closed_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.session_closed(host="h1", user="u", backend="fake", session_id="s1", reason="reap")
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.SESSION_CLOSED.value)
        assert row["details"]["reason"] == "reap"

    def test_keys_sent_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.keys_sent(host="h1", backend="fake", session_id="s1", n_chars=5)
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.KEYS_SENT.value)
        assert row["details"]["n"] == 5

    def test_error_logged_with_stage(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.error(host="h1", stage="authenticating", reason="auth_failed", message="bad creds")
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.ERROR.value)
        assert row["details"]["stage"] == "authenticating"
        assert row["details"]["reason"] == "auth_failed"

    def test_ttl_forwarded(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test", default_ttl_hours=1)
        log.session_opened(host="h1", user="u", backend="fake", session_id="s1")
        rows = store.list_observations(host_key="h1")
        assert rows[0]["expires_at_ms"] is not None
