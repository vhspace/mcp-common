"""Idle-session reaper + daemon self-exit trigger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redfish_mcp.kvm.daemon.cache import CacheEntry, SessionCache

Clock = Callable[[], int]
CloseSession = Callable[[CacheEntry], Awaitable[None]]


class IdleReaper:
    def __init__(
        self,
        *,
        cache: SessionCache,
        session_idle_ms: int,
        daemon_idle_ms: int,
        close_session: CloseSession,
        clock: Clock,
    ) -> None:
        self._cache = cache
        self._session_idle_ms = session_idle_ms
        self._daemon_idle_ms = daemon_idle_ms
        self._close = close_session
        self._clock = clock
        self._empty_since_ms: int | None = None
        self._should_exit = False

    async def tick(self) -> None:
        for entry in self._cache.idle_entries(threshold_ms=self._session_idle_ms):
            await self._close(entry)

        is_empty = not self._cache.snapshot()
        now = self._clock()
        if is_empty:
            if self._empty_since_ms is None:
                self._empty_since_ms = now
            elif now - self._empty_since_ms >= self._daemon_idle_ms:
                self._should_exit = True
        else:
            self._empty_since_ms = None

    def should_exit(self) -> bool:
        return self._should_exit
