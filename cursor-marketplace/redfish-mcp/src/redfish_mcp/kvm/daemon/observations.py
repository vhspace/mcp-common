"""Thin wrapper around AgentStateStore for KVM observation kinds.

Phase 1 scaffolding: defined here so phase 2 (#64) can instantiate it in
``DaemonServer.__init__`` and record open/close/error events from the real
backend handlers without touching the wrapper's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from redfish_mcp.agent_state_store import AgentStateStore


class ObservationKind(StrEnum):
    SESSION_OPENED = "kvm_session_opened"
    SESSION_CLOSED = "kvm_session_closed"
    KEYS_SENT = "kvm_keys_sent"
    REAP = "kvm_reap"
    ERROR = "kvm_error"


@dataclass
class ObservationLogger:
    store: AgentStateStore
    reporter_id: str = "kvm-daemon"
    default_ttl_hours: int | None = None

    def session_opened(self, *, host: str, user: str, backend: str, session_id: str) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.SESSION_OPENED,
            summary=f"kvm session opened on {host}",
            details={"host": host, "user": user, "backend": backend, "session_id": session_id},
            tags=[host, backend],
        )

    def session_closed(
        self, *, host: str, user: str, backend: str, session_id: str, reason: str
    ) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.SESSION_CLOSED,
            summary=f"kvm session closed on {host} ({reason})",
            details={
                "host": host,
                "user": user,
                "backend": backend,
                "session_id": session_id,
                "reason": reason,
            },
            tags=[host, backend, reason],
        )

    def keys_sent(self, *, host: str, backend: str, session_id: str, n_chars: int) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.KEYS_SENT,
            summary=f"sent {n_chars} chars to {host}",
            details={"host": host, "backend": backend, "session_id": session_id, "n": n_chars},
            tags=[host, backend],
        )

    def error(self, *, host: str, stage: str, reason: str, message: str) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.ERROR,
            summary=f"kvm error on {host}: {reason}",
            details={"host": host, "stage": stage, "reason": reason, "message": message},
            tags=[host, reason],
        )

    def _add(
        self,
        *,
        host_key: str,
        kind: ObservationKind,
        summary: str,
        details: dict[str, Any],
        tags: list[str],
    ) -> None:
        self.store.add_observation(
            host_key=host_key,
            kind=str(kind),
            summary=summary,
            details=details,
            tags=tags,
            confidence=None,
            reporter_id=self.reporter_id,
            ttl_hours=self.default_ttl_hours,
        )
