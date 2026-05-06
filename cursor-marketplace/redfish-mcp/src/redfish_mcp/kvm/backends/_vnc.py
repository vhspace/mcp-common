"""Thin wrapper over vncdotool (sync library wrapped in to_thread).

vncdotool is synchronous; we wrap every call in asyncio.to_thread so the
daemon's event loop stays responsive. VncSession holds the sync client
instance plus an asyncio.Lock to serialize concurrent calls (the sync
client is not thread-safe under concurrent sends).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any

from vncdotool import api  # type: ignore[import-untyped]


@dataclass
class VncSession:
    client: Any  # vncdotool SynchronousVNCDoToolClient
    lock: asyncio.Lock

    async def close(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self.client.disconnect)


async def connect(host: str, port: int, password: str) -> VncSession:
    target = f"{host}::{port}"
    client = await asyncio.to_thread(api.connect, target, password=password)
    return VncSession(client=client, lock=asyncio.Lock())


async def screenshot(session: VncSession) -> bytes:
    async with session.lock:

        def _capture() -> bytes:
            session.client.refreshScreen()
            buf = io.BytesIO()
            session.client.screen.save(buf, format="PNG")
            return buf.getvalue()

        return await asyncio.to_thread(_capture)


async def sendkey(session: VncSession, key: str, modifiers: list[str] | None = None) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")


async def sendkeys(session: VncSession, text: str) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")
