"""KVMBackend protocol and supporting types.

Backends implement this protocol. Consumers (the daemon) depend only on the
protocol, never on a concrete implementation, so the Java backend and future
Playwright backend are drop-in interchangeable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionHandle:
    """Opaque handle returned by ``KVMBackend.open``."""

    session_id: str
    host: str
    user: str
    backend: str
    opened_at_ms: int


@dataclass(frozen=True)
class ProgressEvent:
    """One progress tick emitted by a backend during ``open()``."""

    stage: str
    detail: str = ""


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]
"""Async callable that a backend invokes to report progress."""


@runtime_checkable
class KVMBackend(Protocol):
    """Protocol every KVM backend must satisfy."""

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle: ...

    async def screenshot(self, session: SessionHandle) -> bytes: ...

    async def sendkeys(self, session: SessionHandle, text: str) -> None: ...

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None: ...

    async def close(self, session: SessionHandle) -> None: ...

    async def health(self, session: SessionHandle) -> str: ...
