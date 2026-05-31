"""Tests for the asyncio UNIX-socket server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.server import DaemonServer


def _cfg(dir_: Path) -> KVMConfig:
    return KVMConfig(
        socket_dir=dir_,
        session_idle_s=300,
        daemon_idle_s=1,  # short for test shutdown
        max_concurrent=4,
        backend="java",
        java_bin="java",
        jar_cache_dir=dir_ / "jars",
        log_level="INFO",
        daemon_path=None,
    )


@pytest.mark.anyio
async def test_server_starts_and_replies_to_ping(tmp_path: Path, mock_runtime_deps):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)

    async def handle_ping(_params):
        return {"pong": True}

    server.router.register("ping", handle_ping)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.lifecycle.socket_path))
        writer.write(json.dumps({"id": 1, "method": "ping", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        resp = json.loads(line)
        assert resp["id"] == 1
        assert resp["result"] == {"pong": True}
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_server_socket_permissions_0600(tmp_path: Path, mock_runtime_deps):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        mode = server.lifecycle.socket_path.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_server_writes_pid_file(tmp_path: Path, mock_runtime_deps):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        pid_text = server.lifecycle.pid_path.read_text().strip()
        assert pid_text.isdigit()
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_unknown_method_returns_method_not_found(tmp_path: Path, mock_runtime_deps):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.lifecycle.socket_path))
        writer.write(json.dumps({"id": 5, "method": "zzz", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        resp = json.loads(line)
        assert resp["id"] == 5
        assert resp["error"]["code"] == "method_not_found"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"
