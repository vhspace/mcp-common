"""Per-daemon in-memory session cache keyed by (host, user, backend)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from redfish_mcp.kvm.backend import SessionHandle

Clock = Callable[[], int]


@dataclass
class CacheEntry:
    handle: SessionHandle
    last_activity_ms: int
    # Phase 2 (#64): asyncio.Lock used to serialize concurrent open() calls for the same key.
    open_lock: object = field(default=None, repr=False)

    def touch(self, now_ms: int) -> None:
        self.last_activity_ms = now_ms


class SessionCache:
    """Thread-safe map of (host, user, backend) → CacheEntry."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._by_key: dict[tuple[str, str, str], CacheEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(host: str, user: str, backend: str) -> tuple[str, str, str]:
        return (host, user, backend)

    def put(self, host: str, user: str, backend: str, handle: SessionHandle) -> CacheEntry:
        entry = CacheEntry(handle=handle, last_activity_ms=self._clock())
        with self._lock:
            self._by_key[self._key(host, user, backend)] = entry
        return entry

    def get(self, host: str, user: str, backend: str) -> CacheEntry | None:
        with self._lock:
            entry = self._by_key.get(self._key(host, user, backend))
            if entry is not None:
                entry.touch(self._clock())
            return entry

    def pop(self, host: str, user: str, backend: str) -> CacheEntry | None:
        with self._lock:
            return self._by_key.pop(self._key(host, user, backend), None)

    def snapshot(self) -> list[CacheEntry]:
        with self._lock:
            return list(self._by_key.values())

    def idle_entries(self, *, threshold_ms: int) -> list[CacheEntry]:
        cutoff = self._clock() - threshold_ms
        with self._lock:
            return [e for e in self._by_key.values() if e.last_activity_ms < cutoff]
