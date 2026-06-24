"""UNIX-socket client for the KVM daemon."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from redfish_mcp.kvm.exceptions import (
    AuthFailedError,
    BackendUnsupportedError,
    DaemonUnavailableError,
    JarMismatchError,
    JnlpUnavailableError,
    KVMError,
    SessionLostError,
    SlotBusyError,
    StaleSessionError,
)
from redfish_mcp.kvm.protocol import (
    ErrorPayload,
    Request,
    Response,
    decode_message,
    encode_message,
)

ProgressHandler = Callable[[dict[str, Any]], Awaitable[None]]

_ERROR_CODE_TO_EXC: dict[str, type[KVMError]] = {
    "auth_failed": AuthFailedError,
    "kvm_slot_busy": SlotBusyError,
    "stale": StaleSessionError,
    "session_lost": SessionLostError,
    "backend_unsupported": BackendUnsupportedError,
    "jar_mismatch": JarMismatchError,
    "jnlp_unavailable": JnlpUnavailableError,
    "daemon_unavailable": DaemonUnavailableError,
}


class DaemonClient:
    """Async client that speaks line-framed JSON to a single daemon socket."""

    def __init__(self, *, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._id_gen = itertools.count(1)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_progress: ProgressHandler | None = None,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=timeout_s,
            )
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonUnavailableError(f"daemon socket unavailable: {exc}") from exc

        try:
            req = Request(id=next(self._id_gen), method=method, params=params or {})
            writer.write(encode_message(req))
            await writer.drain()

            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
                if not line:
                    raise SessionLostError("daemon closed connection before response")
                msg = decode_message(line)
                if not isinstance(msg, Response):
                    raise KVMError("unexpected request from daemon")
                if msg.id != req.id:
                    continue
                if msg.progress is not None:
                    if on_progress is not None:
                        await on_progress(msg.progress)
                    continue
                if msg.error is not None:
                    self._raise_from_error(msg.error)
                assert msg.result is not None
                return msg.result
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    def _raise_from_error(err: ErrorPayload) -> None:
        cls = _ERROR_CODE_TO_EXC.get(err.code, KVMError)
        raise cls(err.message, stage=err.stage, reason=err.code)
