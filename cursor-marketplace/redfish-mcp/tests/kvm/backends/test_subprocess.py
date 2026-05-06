"""Tests for SessionSubprocesses.

These tests spawn real Xvfb/x11vnc processes and are gated with the
``subprocess`` pytest marker. They auto-skip when Xvfb is not installed.
"""

from __future__ import annotations

import asyncio
import shutil
import socket

import pytest

from redfish_mcp.kvm.backends._subprocess import (
    SessionSubprocesses,
    SpawnedSession,
)

pytestmark = pytest.mark.subprocess


def _xvfb_available() -> bool:
    return shutil.which("Xvfb") is not None and shutil.which("x11vnc") is not None


skip_no_binaries = pytest.mark.skipif(
    not _xvfb_available(),
    reason="Xvfb/x11vnc not installed",
)


@skip_no_binaries
@pytest.mark.anyio
async def test_start_xvfb_and_x11vnc_only():
    """Spin up Xvfb + x11vnc without Java; verify VNC port is listening."""
    session = SessionSubprocesses.for_x11_only(geometry="640x480x24")
    async with session as spawned:
        assert isinstance(spawned, SpawnedSession)
        assert spawned.display_num >= 10
        assert spawned.vnc_port > 0
        # VNC port should be bound to localhost.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect(("127.0.0.1", spawned.vnc_port))
        finally:
            sock.close()


@skip_no_binaries
@pytest.mark.anyio
async def test_cleanup_kills_all_subprocesses():
    session = SessionSubprocesses.for_x11_only(geometry="640x480x24")
    async with session as spawned:
        xvfb_pid = spawned.xvfb.pid
        x11vnc_pid = spawned.x11vnc.pid
    # After exit both processes should be gone.
    await asyncio.sleep(0.2)
    for pid in (xvfb_pid, x11vnc_pid):
        try:
            import os

            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive, f"pid {pid} still alive after __aexit__"


@pytest.mark.anyio
async def test_allocate_free_vnc_port_is_unused():
    """Pure-function test — doesn't need Xvfb."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_tcp_port

    port = _allocate_free_tcp_port()
    assert 1024 < port < 65536
    # Confirm port is actually free by binding.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.close()


@pytest.mark.anyio
async def test_allocate_free_display_num_skips_existing_locks(tmp_path, monkeypatch):
    """Pure-function test — monkeypatches the lock-scan root."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_display

    # Simulate :10 and :11 already in use.
    (tmp_path / ".X10-lock").touch()
    (tmp_path / ".X11-lock").touch()
    monkeypatch.setattr("redfish_mcp.kvm.backends._subprocess._X_LOCK_DIR", tmp_path)
    display = _allocate_free_display(start=10)
    assert display == 12


def test_display_range_start_env_override(tmp_path, monkeypatch):
    """REDFISH_KVM_DISPLAY_RANGE_START shifts the default range."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_display

    monkeypatch.setenv("REDFISH_KVM_DISPLAY_RANGE_START", "50")
    monkeypatch.setattr("redfish_mcp.kvm.backends._subprocess._X_LOCK_DIR", tmp_path)
    display = _allocate_free_display()  # no start= kwarg → env applies
    assert display == 50
