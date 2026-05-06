"""Progress pub/sub: fan out ProgressEvents to N subscribers per session key."""

from __future__ import annotations

import asyncio

from redfish_mcp.kvm.backend import ProgressEvent


class ProgressPublisher:
    """Per-session fanout of progress events.

    ``None`` placed on a queue is the sentinel meaning "session open complete".
    """

    def __init__(self) -> None:
        self._by_session: dict[str, list[asyncio.Queue[ProgressEvent | None]]] = {}

    def subscribe(self, session_key: str) -> asyncio.Queue[ProgressEvent | None]:
        q: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        self._by_session.setdefault(session_key, []).append(q)
        return q

    def unsubscribe(self, session_key: str, q: asyncio.Queue[ProgressEvent | None]) -> None:
        subs = self._by_session.get(session_key)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                self._by_session.pop(session_key, None)

    async def publish(self, session_key: str, event: ProgressEvent) -> None:
        for q in list(self._by_session.get(session_key, [])):
            await q.put(event)

    async def complete(self, session_key: str) -> None:
        for q in list(self._by_session.get(session_key, [])):
            await q.put(None)
        self._by_session.pop(session_key, None)
