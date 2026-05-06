"""In-memory KVMBackend for tests."""

from __future__ import annotations

import itertools
import struct
import time
import zlib

from redfish_mcp.kvm.backend import ProgressCallback, ProgressEvent, SessionHandle
from redfish_mcp.kvm.exceptions import KVMError

_STAGES = (
    "authenticating",
    "fetching_jar",
    "starting_xvfb",
    "launching_java",
    "starting_vnc",
    "handshaking",
    "ready",
)

# Minimal 1x1 PNG so screenshot() returns real PNG bytes.
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    + struct.pack(">I", zlib.crc32(b"IHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"))
    + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    + b"\x0d\n-\xb4"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeBackend:
    """Backend that records calls; optionally fails at a given stage."""

    def __init__(
        self,
        *,
        fail_on_stage: str | None = None,
        fail_as: type[KVMError] = KVMError,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._fail_stage = fail_on_stage
        self._fail_cls = fail_as
        self._ids = (f"fake-{n}" for n in itertools.count(1))

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle:
        self.calls.append(("open", host, user))
        for stage in _STAGES:
            if self._fail_stage == stage:
                raise self._fail_cls(f"fake failure at {stage}", stage=stage)
            await progress(ProgressEvent(stage=stage))
        return SessionHandle(
            session_id=next(self._ids),
            host=host,
            user=user,
            backend="fake",
            opened_at_ms=int(time.time() * 1000),
        )

    async def screenshot(self, session: SessionHandle) -> bytes:
        self.calls.append(("screenshot", session.session_id))
        return _PNG_1X1

    async def sendkeys(self, session: SessionHandle, text: str) -> None:
        self.calls.append(("sendkeys", session.session_id, text))

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None:
        self.calls.append(("sendkey", session.session_id, key, tuple(modifiers or [])))

    async def close(self, session: SessionHandle) -> None:
        self.calls.append(("close", session.session_id))

    async def health(self, session: SessionHandle) -> str:
        self.calls.append(("health", session.session_id))
        return "ok"
