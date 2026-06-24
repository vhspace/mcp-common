"""Tests for kvm.exceptions module."""

from __future__ import annotations

import pytest

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


class TestExceptionHierarchy:
    def test_all_inherit_from_kvm_error(self):
        subclasses = [
            AuthFailedError,
            SlotBusyError,
            StaleSessionError,
            SessionLostError,
            BackendUnsupportedError,
            JarMismatchError,
            JnlpUnavailableError,
            DaemonUnavailableError,
        ]
        for cls in subclasses:
            assert issubclass(cls, KVMError), f"{cls.__name__} must inherit KVMError"

    def test_kvm_error_has_stage_and_reason(self):
        err = KVMError("boom", stage="launching_java", reason="kvm_slot_busy")
        assert str(err) == "boom"
        assert err.stage == "launching_java"
        assert err.reason == "kvm_slot_busy"

    def test_kvm_error_stage_reason_optional(self):
        err = KVMError("no context")
        assert err.stage is None
        assert err.reason is None

    def test_subclasses_preset_reason(self):
        cases: list[tuple[type[KVMError], str]] = [
            (AuthFailedError, "auth_failed"),
            (SlotBusyError, "kvm_slot_busy"),
            (StaleSessionError, "stale"),
            (SessionLostError, "session_lost"),
            (BackendUnsupportedError, "backend_unsupported"),
            (JarMismatchError, "jar_mismatch"),
            (JnlpUnavailableError, "jnlp_unavailable"),
            (DaemonUnavailableError, "daemon_unavailable"),
        ]
        for cls, expected_reason in cases:
            err = cls("msg")
            assert err.reason == expected_reason, cls.__name__

    def test_kvm_error_can_be_raised(self):
        with pytest.raises(AuthFailedError) as exc_info:
            raise AuthFailedError("bad creds", stage="authenticating")
        assert exc_info.value.reason == "auth_failed"
        assert exc_info.value.stage == "authenticating"
