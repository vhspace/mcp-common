"""Tests for daemon lifecycle helpers."""

from __future__ import annotations

import os
from pathlib import Path

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import (
    DaemonLifecycle,
    is_process_alive,
)


def _cfg(socket_dir: Path, **overrides: object) -> KVMConfig:
    return KVMConfig(
        socket_dir=socket_dir,
        session_idle_s=int(overrides.get("session_idle_s", 300)),
        daemon_idle_s=int(overrides.get("daemon_idle_s", 600)),
        max_concurrent=int(overrides.get("max_concurrent", 4)),
        backend=str(overrides.get("backend", "java")),
        java_bin=str(overrides.get("java_bin", "java")),
        jar_cache_dir=Path(overrides.get("jar_cache_dir", socket_dir / "jars")),  # type: ignore[arg-type]
        log_level=str(overrides.get("log_level", "INFO")),
        daemon_path=None,
    )


class TestPaths:
    def test_socket_and_pid_paths_contain_uid(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        uid = os.getuid()
        assert lc.socket_path == tmp_path / f"redfish-mcp-kvm-{uid}.sock"
        assert lc.pid_path == tmp_path / f"redfish-mcp-kvm-{uid}.pid"


class TestIsProcessAlive:
    def test_pid_0_is_never_alive(self):
        assert not is_process_alive(0)

    def test_self_is_alive(self):
        assert is_process_alive(os.getpid())

    def test_absurdly_large_pid_is_dead(self):
        assert not is_process_alive(99_999_999)


class TestClaimedBy:
    def test_missing_pid_file_means_no_claim(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        assert lc.claimed_by_live_daemon() is False

    def test_stale_pid_file_is_removed(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text("99999999\n")
        lc.socket_path.touch()
        assert lc.claimed_by_live_daemon() is False
        assert not lc.pid_path.exists()
        assert not lc.socket_path.exists()

    def test_live_pid_is_honored(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text(f"{os.getpid()}\n")
        lc.socket_path.touch()
        assert lc.claimed_by_live_daemon() is True
        assert lc.pid_path.exists()


class TestWriteAndClear:
    def test_write_pid_sets_0600(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.write_pid(4242)
        assert lc.pid_path.read_text().strip() == "4242"
        mode = lc.pid_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_clear_removes_both(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text("1\n")
        lc.socket_path.touch()
        lc.clear()
        assert not lc.pid_path.exists()
        assert not lc.socket_path.exists()
