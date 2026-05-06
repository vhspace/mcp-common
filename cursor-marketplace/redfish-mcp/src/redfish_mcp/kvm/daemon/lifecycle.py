"""Socket + PID file lifecycle for the KVM daemon."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from redfish_mcp.kvm.config import KVMConfig


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


@dataclass
class DaemonLifecycle:
    config: KVMConfig

    @property
    def _uid(self) -> int:
        return os.getuid()

    @property
    def socket_path(self) -> Path:
        return self.config.socket_dir / f"redfish-mcp-kvm-{self._uid}.sock"

    @property
    def pid_path(self) -> Path:
        return self.config.socket_dir / f"redfish-mcp-kvm-{self._uid}.pid"

    def claimed_by_live_daemon(self) -> bool:
        if not self.pid_path.exists():
            return False
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            self.clear()
            return False
        if is_process_alive(pid):
            return True
        self.clear()
        return False

    def write_pid(self, pid: int) -> None:
        self.config.socket_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pid_path.with_suffix(".pid.tmp")
        tmp.write_text(f"{pid}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.pid_path)

    def clear(self) -> None:
        for p in (self.pid_path, self.socket_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
