"""Per-session Xvfb + Java + x11vnc subprocess lifecycle.

Design commitment: all three subprocesses are co-scoped in a single
async context manager. If any start step fails, or if the caller's
body raises, everything already-started is torn down in reverse order
(x11vnc → java → Xvfb) with SIGTERM then SIGKILL after a grace period.

Phase 2 ships ``for_x11_only()`` (no Java) for the tier-2 integration
test and for the VNC library spike. The full ``for_java_ikvm()`` factory
lands in Task 8's JavaIkvmBackend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

logger = logging.getLogger("redfish_mcp.kvm.backends.subprocess")

_X_LOCK_DIR = Path("/tmp")
_SIGTERM_GRACE_S = 2.0


def _default_display_range_start() -> int:
    """Read REDFISH_KVM_DISPLAY_RANGE_START with fallback to 10.

    Read at call time (not at module import) so tests that monkeypatch the
    env var after import work correctly.
    """
    raw = os.getenv("REDFISH_KVM_DISPLAY_RANGE_START")
    if raw is None or raw == "":
        return 10
    try:
        return int(raw)
    except ValueError:
        return 10


@dataclass
class SpawnedSession:
    display_num: int
    vnc_port: int
    vnc_secret_path: Path
    xvfb: asyncio.subprocess.Process
    java: asyncio.subprocess.Process | None
    x11vnc: asyncio.subprocess.Process


def _allocate_free_tcp_port() -> int:
    """Bind a random free TCP port on localhost and release it immediately."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _allocate_free_display(*, start: int | None = None, end: int = 100) -> int:
    """Find an X display number with no /tmp/.X<n>-lock file.

    ``start`` defaults to REDFISH_KVM_DISPLAY_RANGE_START (or 10).
    """
    if start is None:
        start = _default_display_range_start()
    for n in range(start, end):
        if not (_X_LOCK_DIR / f".X{n}-lock").exists():
            return n
    raise RuntimeError(f"no free X display number in [{start}, {end})")


class SessionSubprocesses:
    """Co-scoped Xvfb+(Java?)+x11vnc lifecycle as async context manager."""

    def __init__(
        self,
        *,
        geometry: str = "1280x1024x24",
        java_cmd: list[str] | None = None,
        tmp_dir: Path | None = None,
    ) -> None:
        self.geometry = geometry
        self.java_cmd = java_cmd
        self._tmp_dir = tmp_dir or Path("/tmp")
        self._spawned: SpawnedSession | None = None

    @classmethod
    def for_x11_only(cls, *, geometry: str = "1280x1024x24") -> SessionSubprocesses:
        """Spawn only Xvfb + x11vnc. Used for VNC-lib testing/spike."""
        return cls(geometry=geometry, java_cmd=None)

    @classmethod
    def for_java_ikvm(
        cls,
        *,
        java_cmd: list[str],
        geometry: str = "1280x1024x24",
    ) -> SessionSubprocesses:
        """Spawn Xvfb, launch the Java iKVM JAR, then expose via x11vnc."""
        return cls(geometry=geometry, java_cmd=java_cmd)

    async def __aenter__(self) -> SpawnedSession:
        display = _allocate_free_display()
        vnc_port = _allocate_free_tcp_port()

        secret_bytes = secrets.token_bytes(32).hex().encode()
        secret_path = self._tmp_dir / f"x11vnc-pw-{os.getpid()}-{display}"
        secret_path.write_bytes(secret_bytes)
        os.chmod(secret_path, 0o600)

        env = {**os.environ, "DISPLAY": f":{display}"}

        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb",
            f":{display}",
            "-screen",
            "0",
            self.geometry,
            "-nolisten",
            "tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

        # Wait briefly for the lock file to appear.
        await _wait_for_path(_X_LOCK_DIR / f".X{display}-lock", timeout_s=3.0)

        java = None
        try:
            if self.java_cmd is not None:
                java = await asyncio.create_subprocess_exec(
                    *self.java_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                # Give Java a moment to initialize; detect immediate exit.
                await asyncio.sleep(1.0)
                if java.returncode is not None:
                    raise RuntimeError(f"java exited during startup rc={java.returncode}")

            x11vnc = await asyncio.create_subprocess_exec(
                "x11vnc",
                "-display",
                f":{display}",
                "-localhost",
                "-rfbport",
                str(vnc_port),
                "-passwdfile",
                str(secret_path),
                "-forever",
                "-quiet",
                "-noxdamage",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )

            # Wait for x11vnc to accept a connection.
            await _wait_for_tcp("127.0.0.1", vnc_port, timeout_s=3.0)

            self._spawned = SpawnedSession(
                display_num=display,
                vnc_port=vnc_port,
                vnc_secret_path=secret_path,
                xvfb=xvfb,
                java=java,
                x11vnc=x11vnc,
            )
            return self._spawned
        except BaseException:
            # Partial cleanup of whatever we spawned before the failure.
            for proc in (java, xvfb):
                if proc is not None and proc.returncode is None:
                    await _terminate_process(proc)
            try:
                secret_path.unlink()
            except FileNotFoundError:
                pass
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._spawned is None:
            return
        s = self._spawned
        # Tear down in reverse order: x11vnc, java, Xvfb.
        for proc in (s.x11vnc, s.java, s.xvfb):
            if proc is None:
                continue
            if proc.returncode is None:
                await _terminate_process(proc)
        try:
            s.vnc_secret_path.unlink()
        except FileNotFoundError:
            pass
        self._spawned = None


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SIGTERM_GRACE_S)
    except TimeoutError:
        try:
            proc.send_signal(signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except TimeoutError:
            logger.warning("process pid=%s did not exit after SIGKILL", proc.pid)


async def _wait_for_path(path: Path, *, timeout_s: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {path}")


async def _wait_for_tcp(host: str, port: int, *, timeout_s: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.05)
    raise RuntimeError(f"timed out waiting for TCP {host}:{port}")
