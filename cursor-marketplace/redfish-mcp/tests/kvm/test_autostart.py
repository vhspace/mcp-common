"""Tests for ensure_daemon_running."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from redfish_mcp.kvm.autostart import ensure_daemon_running
from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle


def _cfg(tmp: Path) -> KVMConfig:
    return KVMConfig(
        socket_dir=tmp,
        session_idle_s=300,
        daemon_idle_s=1,
        max_concurrent=4,
        backend="java",
        java_bin="java",
        jar_cache_dir=tmp / "jars",
        log_level="INFO",
        daemon_path=None,
    )


@pytest.mark.anyio
async def test_noop_when_daemon_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    lc = DaemonLifecycle(cfg)
    lc.pid_path.write_text(f"{os.getpid()}\n")
    lc.socket_path.touch()

    called = {"spawned": False}

    def fake_spawn(_cfg):
        called["spawned"] = True

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", fake_spawn)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _true_async)

    await ensure_daemon_running(cfg)
    assert called["spawned"] is False


@pytest.mark.anyio
async def test_spawns_when_no_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)

    spawned = {"n": 0}

    def fake_spawn(_cfg):
        spawned["n"] += 1
        DaemonLifecycle(_cfg).pid_path.write_text(f"{os.getpid()}\n")
        DaemonLifecycle(_cfg).socket_path.touch()

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", fake_spawn)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _true_async)

    await ensure_daemon_running(cfg)
    assert spawned["n"] == 1


@pytest.mark.anyio
async def test_raises_when_socket_never_appears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.exceptions import DaemonUnavailableError

    cfg = _cfg(tmp_path)

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", lambda _c: None)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _false_async)

    with pytest.raises(DaemonUnavailableError):
        await ensure_daemon_running(cfg, start_timeout_s=0.05)


def test_spawn_daemon_uses_daemon_path_override(tmp_path: Path):
    from redfish_mcp.kvm import autostart

    called: dict[str, list[str]] = {}

    class _FakePopen:
        def __init__(self, cmd, **_kwargs):
            called["cmd"] = list(cmd)

    override = tmp_path / "my-daemon"
    override.touch()
    cfg = KVMConfig(
        socket_dir=tmp_path,
        session_idle_s=300,
        daemon_idle_s=1,
        max_concurrent=4,
        backend="java",
        java_bin="java",
        jar_cache_dir=tmp_path / "jars",
        log_level="INFO",
        daemon_path=override,
    )

    original_popen = autostart.subprocess.Popen
    autostart.subprocess.Popen = _FakePopen  # type: ignore[assignment]
    try:
        autostart._spawn_daemon(cfg)
    finally:
        autostart.subprocess.Popen = original_popen  # type: ignore[assignment]

    assert called["cmd"] == [str(override)]


async def _true_async(*_args, **_kwargs) -> bool:
    return True


async def _false_async(*_args, **_kwargs) -> bool:
    return False


@pytest.fixture
def anyio_backend():
    return "asyncio"
