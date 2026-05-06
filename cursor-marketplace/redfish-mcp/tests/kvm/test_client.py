"""Tests for DaemonClient."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redfish_mcp.kvm.client import DaemonClient
from redfish_mcp.kvm.exceptions import AuthFailedError, KVMError


class FakeServer:
    def __init__(self, socket_path: Path, responses: list[dict]) -> None:
        self.socket_path = socket_path
        self._responses = responses
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line)
        for item in self._responses:
            item_with_id = dict(item)
            item_with_id["id"] = req["id"]
            writer.write(json.dumps(item_with_id).encode() + b"\n")
            await writer.drain()
        writer.close()
        await writer.wait_closed()


@pytest.mark.anyio
async def test_request_returns_result(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(sock, [{"result": {"ok": True, "val": 1}}])
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        result = await client.request("ping", {})
        assert result == {"ok": True, "val": 1}
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_request_error_maps_to_kvmerror(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(
        sock,
        [{"error": {"code": "auth_failed", "message": "bad creds", "stage": "authenticating"}}],
    )
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        with pytest.raises(AuthFailedError) as exc_info:
            await client.request("open", {})
        assert exc_info.value.stage == "authenticating"
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_unknown_error_code_falls_back_to_base(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(sock, [{"error": {"code": "weird", "message": "x", "stage": None}}])
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        with pytest.raises(KVMError) as exc_info:
            await client.request("open", {})
        # Unknown code is preserved as reason on the base KVMError.
        assert type(exc_info.value) is KVMError
        assert exc_info.value.reason == "weird"
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_progress_stream_yields_events_until_result(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(
        sock,
        [
            {"progress": {"stage": "authenticating", "detail": ""}},
            {"progress": {"stage": "ready", "detail": ""}},
            {"result": {"session_id": "s1"}},
        ],
    )
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        events: list[dict] = []

        async def on_progress(ev: dict) -> None:
            events.append(ev)

        result = await client.request("open", {}, on_progress=on_progress)
        assert result == {"session_id": "s1"}
        assert [e["stage"] for e in events] == ["authenticating", "ready"]
    finally:
        await server.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"
