"""Router-layer wrappers around backend.open()/screenshot() with timeout + stage tracking.

The KVMBackend Protocol stays unchanged — timeout enforcement lives here so
every backend (Java in phase 2, Playwright in v2) gets uniform semantics
without re-implementing wait_for plumbing.

On timeout in open(), we report the last ProgressEvent stage that fired
before the wait_for fired, so clients see failed:timeout:<stage> rather
than a naked timeout.
"""

from __future__ import annotations

import asyncio
import os

from redfish_mcp.kvm.backend import KVMBackend, ProgressEvent, SessionHandle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.exceptions import StaleSessionError


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def default_open_timeout_s() -> float:
    """Read REDFISH_KVM_OPEN_TIMEOUT_S at call time (with fallback 30.0)."""
    return _env_float("REDFISH_KVM_OPEN_TIMEOUT_S", 30.0)


def default_screenshot_timeout_s() -> float:
    """Read REDFISH_KVM_SCREENSHOT_TIMEOUT_S at call time (with fallback 15.0)."""
    return _env_float("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", 15.0)


async def open_session(
    *,
    backend: KVMBackend,
    progress: ProgressPublisher,
    host: str,
    user: str,
    password: str,
    session_key: str,
    timeout_s: float | None = None,
) -> SessionHandle:
    """Call backend.open() with a bounded timeout and stage-aware error.

    The returned handle is the one backend.open() produced. On timeout, a
    StaleSessionError is raised with ``stage=<last-seen stage>``. When
    ``timeout_s`` is None, the value from ``REDFISH_KVM_OPEN_TIMEOUT_S``
    (fallback 30.0) is used.
    """
    if timeout_s is None:
        timeout_s = default_open_timeout_s()
    last_stage = "authenticating"

    async def tracking_progress(event: ProgressEvent) -> None:
        nonlocal last_stage
        last_stage = event.stage
        await progress.publish(session_key, event)

    try:
        handle = await asyncio.wait_for(
            backend.open(host, user, password, tracking_progress),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise StaleSessionError(
            f"open() did not complete within {timeout_s}s",
            stage=last_stage,
        ) from exc
    finally:
        await progress.complete(session_key)

    return handle


async def screenshot_session(
    *,
    backend: KVMBackend,
    session: SessionHandle,
    timeout_s: float | None = None,
) -> bytes:
    """Call backend.screenshot() with a shorter bounded timeout.

    Steady-state captures should be fast (sub-second on LAN). Anything
    over ``timeout_s`` indicates a hung Java process or dead VNC channel.
    When ``timeout_s`` is None, the value from
    ``REDFISH_KVM_SCREENSHOT_TIMEOUT_S`` (fallback 15.0) is used.
    """
    if timeout_s is None:
        timeout_s = default_screenshot_timeout_s()
    try:
        return await asyncio.wait_for(
            backend.screenshot(session),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise StaleSessionError(
            f"screenshot did not complete within {timeout_s}s",
            stage="ready",
        ) from exc
