"""Error hierarchy for the KVM console feature.

Subclasses carry a preset ``reason`` token so callers can pattern-match without
string comparisons. ``stage`` tracks which cold-start stage failed (see spec).
"""

from __future__ import annotations


class KVMError(Exception):
    """Base class for all KVM-feature errors."""

    reason: str | None = None

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        if reason is not None:
            self.reason = reason


class AuthFailedError(KVMError):
    reason = "auth_failed"


class SlotBusyError(KVMError):
    reason = "kvm_slot_busy"


class StaleSessionError(KVMError):
    reason = "stale"


class SessionLostError(KVMError):
    reason = "session_lost"


class BackendUnsupportedError(KVMError):
    reason = "backend_unsupported"


class JarMismatchError(KVMError):
    reason = "jar_mismatch"


class JnlpUnavailableError(KVMError):
    reason = "jnlp_unavailable"


class DaemonUnavailableError(KVMError):
    reason = "daemon_unavailable"
