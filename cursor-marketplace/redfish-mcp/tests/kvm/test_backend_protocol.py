"""Tests for the KVMBackend protocol and its supporting types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict

import pytest

from redfish_mcp.kvm.backend import (
    KVMBackend,
    ProgressCallback,
    ProgressEvent,
    SessionHandle,
)


class TestSessionHandle:
    def test_handle_is_frozen_with_expected_fields(self):
        h = SessionHandle(
            session_id="sess-1",
            host="10.0.0.1",
            user="admin",
            backend="java",
            opened_at_ms=123456,
        )
        assert h.session_id == "sess-1"
        assert h.host == "10.0.0.1"
        assert h.user == "admin"
        assert h.backend == "java"
        assert h.opened_at_ms == 123456
        with pytest.raises(FrozenInstanceError):
            h.host = "other"  # type: ignore[misc]


class TestProgressEvent:
    def test_roundtrip_via_asdict(self):
        e = ProgressEvent(stage="ready", detail="")
        assert asdict(e) == {"stage": "ready", "detail": ""}


class TestKVMBackendProtocol:
    def test_a_class_with_correct_methods_satisfies_protocol(self):
        class MiniBackend:
            async def open(
                self,
                host: str,
                user: str,
                password: str,
                progress: ProgressCallback,
            ) -> SessionHandle:
                return SessionHandle(
                    session_id="x", host=host, user=user, backend="mini", opened_at_ms=0
                )

            async def screenshot(self, session: SessionHandle) -> bytes:
                return b""

            async def sendkeys(self, session: SessionHandle, text: str) -> None:
                return None

            async def sendkey(
                self, session: SessionHandle, key: str, modifiers: list[str] | None = None
            ) -> None:
                return None

            async def close(self, session: SessionHandle) -> None:
                return None

            async def health(self, session: SessionHandle) -> str:
                return "ok"

        b: KVMBackend = MiniBackend()
        assert isinstance(b, KVMBackend)  # runtime-checkable

    def test_a_class_missing_methods_is_rejected(self):
        class NoOp:
            pass

        assert not isinstance(NoOp(), KVMBackend)
