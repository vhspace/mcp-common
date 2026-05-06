"""Daemon autostart helper used by MCP tools and the CLI."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle
from redfish_mcp.kvm.exceptions import DaemonUnavailableError

logger = logging.getLogger("redfish_mcp.kvm.autostart")


async def ensure_daemon_running(cfg: KVMConfig, *, start_timeout_s: float = 3.0) -> None:
    lc = DaemonLifecycle(cfg)
    if lc.claimed_by_live_daemon() and lc.socket_path.exists():
        return

    _spawn_daemon(cfg)
    if not await _wait_for_socket(lc.socket_path, timeout_s=start_timeout_s):
        raise DaemonUnavailableError(f"daemon did not start within {start_timeout_s}s")


def _spawn_daemon(cfg: KVMConfig) -> None:
    log_path = cfg.socket_dir / "kvm-daemon.log"
    cfg.socket_dir.mkdir(parents=True, exist_ok=True)
    if cfg.daemon_path is not None:
        cmd = [str(cfg.daemon_path)]
    else:
        cmd = [sys.executable, "-m", "redfish_mcp.kvm.daemon"]
    logger.info("spawning kvm daemon: %s", " ".join(cmd))
    with open(log_path, "a") as log_fh:
        subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


async def _wait_for_socket(socket_path: Path, *, timeout_s: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if socket_path.exists():
            try:
                _, writer = await asyncio.open_unix_connection(str(socket_path))
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (ConnectionRefusedError, FileNotFoundError):
                pass
        await asyncio.sleep(0.05)
    return False
