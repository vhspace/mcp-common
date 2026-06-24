"""NVIDIA Cumulus Linux driver (``nv show ... -o json``).

Connection lifecycle:
- **With session()**: one SSH connection is opened at session entry and
  reused for all commands within the block. Commands multiplex as separate
  SSH channels (Cumulus allows ``MaxSessions=10``).
- **Without session()**: each command opens its own SSH connection (the
  pre-optimization default, still used by single-switch tools).

For sites with a jump host, asyncssh's ``tunnel=`` parameter handles
transparent tunneling.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncssh

from mcp_network.drivers.base import ConnectionInfo, NetworkDriverError, SwitchDriver

logger = logging.getLogger(__name__)


CONNECT_TIMEOUT = 10
KEEPALIVE_INTERVAL = 60
KEEPALIVE_COUNT_MAX = 3

_DEFAULT_COMMAND_TIMEOUT = 30.0
"""Per-command execution ceiling (seconds), mirroring ``CONNECT_TIMEOUT``.

``connect_timeout`` bounds how long we wait to *establish* a connection;
this bounds how long a single ``nv show`` / ``journalctl`` may run on an
already-open channel. Without it a wedged switch (command never returns)
would block the call forever — and in the parallel ``find_port_*`` scans
would hold a concurrency slot and stall the whole ``asyncio.gather``.
"""


def _resolve_command_timeout() -> float:
    """Read the per-command timeout from ``MCP_NETWORK_COMMAND_TIMEOUT``.

    Falls back to :data:`_DEFAULT_COMMAND_TIMEOUT` when unset, non-numeric,
    or non-positive. Env-overridable to match the ``MCP_NETWORK_*`` knobs
    used elsewhere (e.g. ``MCP_NETWORK_INVENTORY_DIR``).
    """
    raw = os.environ.get("MCP_NETWORK_COMMAND_TIMEOUT")
    if raw is None:
        return _DEFAULT_COMMAND_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid MCP_NETWORK_COMMAND_TIMEOUT=%r; using default %ss",
            raw,
            _DEFAULT_COMMAND_TIMEOUT,
        )
        return _DEFAULT_COMMAND_TIMEOUT
    if value <= 0:
        logger.warning(
            "MCP_NETWORK_COMMAND_TIMEOUT=%r must be > 0; using default %ss",
            raw,
            _DEFAULT_COMMAND_TIMEOUT,
        )
        return _DEFAULT_COMMAND_TIMEOUT
    return value


COMMAND_TIMEOUT = _resolve_command_timeout()


class CumulusDriver(SwitchDriver):
    """Read-only Cumulus Linux driver (``nv show``, ``journalctl``)."""

    def __init__(self, conn: ConnectionInfo) -> None:
        super().__init__(conn)
        self._ssh: asyncssh.SSHClientConnection | None = None
        self._tunnel: asyncssh.SSHClientConnection | None = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        """Hold one SSH connection for multiple commands within a tool call.

        On exit the connection (and tunnel, if any) is torn down. Safe to
        nest — the inner call is a no-op if a session is already active.
        """
        if self._ssh is not None:
            yield
            return

        tunnel, ssh = await _connect(self.conn)
        self._tunnel = tunnel
        self._ssh = ssh
        try:
            yield
        finally:
            self._ssh = None
            self._tunnel = None
            ssh.close()
            await ssh.wait_closed()
            if tunnel is not None:
                tunnel.close()
                await tunnel.wait_closed()

    async def _run(self, cmd: str) -> str:
        """Run one command, return stdout as text.

        If a ``session()`` is active, reuses the cached connection.
        Otherwise opens a per-call connection.
        """
        if self._ssh is not None:
            return await _exec(self._ssh, cmd, self.conn.host, timeout=COMMAND_TIMEOUT)

        tunnel, ssh = await _connect(self.conn)
        try:
            return await _exec(ssh, cmd, self.conn.host, timeout=COMMAND_TIMEOUT)
        finally:
            ssh.close()
            await ssh.wait_closed()
            if tunnel is not None:
                tunnel.close()
                await tunnel.wait_closed()

    async def _run_json(self, cmd: str) -> Any:
        raw = await self._run(cmd)
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise NetworkDriverError(
                f"JSON parse failure for {cmd!r}: {e}",
                host=self.conn.host,
                hint="Cumulus may not support -o json for this command; check version",
            ) from e

    async def system_info(self) -> dict[str, Any]:
        return await self._run_json("nv show system -o json") or {}

    async def interfaces_brief(self) -> list[dict[str, Any]]:
        data = await self._run_json("nv show interface -o json") or {}
        return _to_list_of_dicts(data, key_field="name")

    async def interface(self, port: str) -> dict[str, Any]:
        _validate_port(port)
        return await self._run_json(f"nv show interface {port} -o json") or {}

    async def interface_counters(self, port: str) -> dict[str, Any]:
        _validate_port(port)
        return await self._run_json(f"nv show interface {port} counters -o json") or {}

    async def lldp(self) -> list[dict[str, Any]]:
        data = await self._run_json("nv show interface --view lldp -o json") or {}
        return _to_list_of_dicts(data, key_field="name")

    async def bgp_summary(self) -> dict[str, Any]:
        return await self._run_json("nv show vrf default router bgp neighbor -o json") or {}

    async def mac_table(self) -> list[dict[str, Any]]:
        data = await self._run_json("nv show bridge domain br_default mac-table -o json")
        return _to_list_of_dicts(data, key_field="id")

    async def logs(
        self,
        lines: int = 75,
        since: str | None = None,
        until: str | None = None,
        unit: str | None = None,
        identifier: str | None = None,
        priority: str | None = None,
        grep: str | None = None,
        boot: bool = False,
        kernel: bool = False,
    ) -> list[dict[str, Any]]:
        lines = max(1, min(lines, MAX_LOG_LINES))
        parts = ["journalctl", "-o", "json", "-r", "--no-pager", "-n", str(lines)]
        if since:
            _validate_arg(since, "since", allow_spaces=True)
            parts += ["--since", since]
        if until:
            _validate_arg(until, "until", allow_spaces=True)
            parts += ["--until", until]
        if unit:
            _validate_arg(unit, "unit")
            parts += ["-u", unit]
        if identifier:
            _validate_arg(identifier, "identifier")
            parts += ["-t", identifier]
        if priority:
            _validate_arg(priority, "priority")
            parts += ["-p", priority]
        if grep:
            _validate_arg(grep, "grep", allow_spaces=True)
            parts += ["--grep", grep]
        if boot:
            parts.append("-b")
        if kernel:
            parts.append("-k")

        raw = await self._run(" ".join(parts))
        return _parse_journal_lines(raw)

    async def wjh(self) -> list[dict[str, Any]]:
        data = await self._run_json("nv show system wjh packet-buffer -o json")
        return _to_list_of_dicts(data or {}, key_field="id")


async def _connect(
    conn: ConnectionInfo,
) -> tuple[asyncssh.SSHClientConnection | None, asyncssh.SSHClientConnection]:
    """Open an asyncssh connection, optionally tunneled through a jump host.

    Returns ``(tunnel_or_None, switch_connection)``.
    """
    tunnel: asyncssh.SSHClientConnection | None = None
    if conn.jump_host:
        if conn.jump_password is None or conn.jump_user is None:
            raise NetworkDriverError(
                f"jump host {conn.jump_host!r} requires jump_user and jump_password",
                host=conn.host,
                hint="check site inventory jump_host.user_env / password_env",
            )
        tunnel = await asyncssh.connect(
            conn.jump_host,
            port=conn.jump_port,
            username=conn.jump_user,
            password=conn.jump_password.get_secret_value(),
            known_hosts=None,
            connect_timeout=CONNECT_TIMEOUT,
            keepalive_interval=KEEPALIVE_INTERVAL,
            keepalive_count_max=KEEPALIVE_COUNT_MAX,
        )

    try:
        ssh = await asyncssh.connect(
            conn.host,
            port=conn.port,
            username=conn.user,
            password=conn.password.get_secret_value(),
            known_hosts=None,
            connect_timeout=CONNECT_TIMEOUT,
            keepalive_interval=KEEPALIVE_INTERVAL,
            keepalive_count_max=KEEPALIVE_COUNT_MAX,
            tunnel=tunnel,
        )
    except asyncssh.PermissionDenied as e:
        if tunnel is not None:
            tunnel.close()
            await tunnel.wait_closed()
        raise NetworkDriverError(
            "SSH permission denied",
            host=conn.host,
            hint="verify credentials env vars named in inventory credentials_env",
        ) from e
    except (asyncssh.Error, OSError) as e:
        if tunnel is not None:
            tunnel.close()
            await tunnel.wait_closed()
        raise NetworkDriverError(
            f"SSH connect failed: {e}",
            host=conn.host,
        ) from e

    return tunnel, ssh


async def _exec(
    ssh: asyncssh.SSHClientConnection,
    cmd: str,
    host: str,
    *,
    timeout: float = COMMAND_TIMEOUT,
) -> str:
    """Run a single command on an open SSH connection.

    ``timeout`` bounds the command end-to-end. On expiry the awaited
    ``ssh.run`` coroutine is cancelled (the channel is torn down when the
    connection closes in the caller's ``finally``) and a ``NetworkDriverError``
    is raised so the failure surfaces as a normal per-host error — critically,
    in the parallel ``find_port_*`` scans this lets the bounding semaphore slot
    release and ``asyncio.gather`` complete instead of hanging on one wedged
    switch.
    """
    try:
        result = await asyncio.wait_for(ssh.run(cmd, check=False), timeout=timeout)
    except TimeoutError as e:
        # asyncio.TimeoutError is an alias of builtin TimeoutError on py3.11+;
        # this also covers asyncssh.TimeoutError (a TimeoutError subclass).
        raise NetworkDriverError(
            f"command {cmd!r} timed out after {timeout}s",
            host=host,
            hint="command timed out",
        ) from e
    except asyncssh.Error as e:
        raise NetworkDriverError(
            f"command failed: {cmd!r}: {e}",
            host=host,
        ) from e
    if result.exit_status:
        stderr = _as_text(result.stderr or "").strip()
        raise NetworkDriverError(
            f"command {cmd!r} exited {result.exit_status}: {stderr}",
            host=host,
        )
    return _as_text(result.stdout or "")


def _as_text(raw: str | bytes) -> str:
    """asyncssh may return bytes for stdout/stderr on some channels."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def _to_list_of_dicts(data: Any, *, key_field: str) -> list[dict[str, Any]]:
    """Normalize Cumulus JSON output.

    ``nv show`` returns a dict keyed by resource id (port name, mac id, etc.).
    We flatten into ``[{key_field: <id>, **rest}, ...]``.
    """
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            out.append({key_field: key, **value})
        else:
            out.append({key_field: key, "value": value})
    return out


MAX_LOG_LINES = 500

_PRIORITY_NAMES = {
    "0": "emerg",
    "1": "alert",
    "2": "crit",
    "3": "err",
    "4": "warning",
    "5": "notice",
    "6": "info",
    "7": "debug",
}

_SHELL_METACHARACTERS = frozenset(";|&`$<>()")


def _validate_port(port: str) -> None:
    """Cheap defense-in-depth against shell injection.

    Cumulus port names look like ``swp29``, ``swp14s1``, ``eth0``, ``bond1``.
    Anything with shell metacharacters gets rejected.
    """
    if not port or any(c in port for c in " \t\n;|&`$<>()"):
        raise NetworkDriverError(f"invalid port name: {port!r}")


def _validate_arg(value: str, name: str, *, allow_spaces: bool = False) -> None:
    """Reject shell metacharacters in journalctl arguments.

    Parameters that never need whitespace (unit, identifier, priority)
    should use the default ``allow_spaces=False``.  Free-form parameters
    like ``since``, ``until``, and ``grep`` need ``allow_spaces=True``.
    """
    if not value or not value.strip():
        raise NetworkDriverError(f"invalid {name} value: {value!r}")
    bad = _SHELL_METACHARACTERS if allow_spaces else _SHELL_METACHARACTERS | frozenset(" \t\n")
    if any(c in value for c in bad):
        raise NetworkDriverError(f"invalid {name} value: {value!r}")


def _parse_journal_lines(raw: str) -> list[dict[str, Any]]:
    """Parse ``journalctl -o json`` output (one JSON object per line).

    Normalizes each entry to a slim dict with consistent field names.
    """
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(_normalize_journal_entry(obj))
    return entries


def _normalize_journal_entry(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract key fields from a raw journalctl JSON object."""
    ts_usec = obj.get("__REALTIME_TIMESTAMP")
    timestamp = None
    if ts_usec:
        try:
            timestamp = (
                datetime.datetime.fromtimestamp(int(ts_usec) / 1_000_000, tz=datetime.UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (ValueError, TypeError, OSError):
            timestamp = str(ts_usec)

    prio_raw = str(obj.get("PRIORITY", ""))
    priority_name = _PRIORITY_NAMES.get(prio_raw, prio_raw)

    return {
        "timestamp": timestamp,
        "priority": priority_name,
        "unit": obj.get("_SYSTEMD_UNIT", ""),
        "identifier": obj.get("SYSLOG_IDENTIFIER", ""),
        "pid": obj.get("_PID", ""),
        "message": obj.get("MESSAGE", ""),
    }
