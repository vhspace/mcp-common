"""asyncio UNIX-socket daemon for the KVM feature."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.cache import CacheEntry, SessionCache
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.reaper import IdleReaper
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.protocol import (
    ProtocolError,
    Request,
    Response,
    decode_message,
    encode_message,
)

logger = logging.getLogger("redfish_mcp.kvm.daemon")


def _now_ms() -> int:
    return int(time.time() * 1000)


class DaemonServer:
    def __init__(self, config: KVMConfig) -> None:
        self.config = config
        self.lifecycle = DaemonLifecycle(config)
        self.cache = SessionCache(clock=_now_ms)
        # Wired by phase 2 (#64) open() handler + phase 3 (#65) subscribe_progress method.
        self.progress = ProgressPublisher()
        self.router = Router()
        self.reaper = IdleReaper(
            cache=self.cache,
            session_idle_ms=config.session_idle_s * 1000,
            daemon_idle_ms=config.daemon_idle_s * 1000,
            close_session=self._close_entry,
            clock=_now_ms,
        )
        self._server: asyncio.AbstractServer | None = None
        self._reaper_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

        # Register KVM handlers on the router. Backend is instantiated here;
        # tests that only exercise the protocol handler (not the full server
        # startup) can swap in FakeBackend via monkey-patching before this runs.
        from redfish_mcp.kvm.daemon.handlers import register_kvm_handlers

        if config.backend == "playwright":
            from redfish_mcp.kvm.backends.playwright_ami import PlaywrightAmiBackend

            self.backend = PlaywrightAmiBackend()
        else:
            from redfish_mcp.kvm.backends.java import JavaIkvmBackend

            self.backend = JavaIkvmBackend(
                jar_cache_root=config.jar_cache_dir,
                java_bin=config.java_bin,
            )
        register_kvm_handlers(
            router=self.router,
            cache=self.cache,
            progress=self.progress,
            backend=self.backend,
        )

    async def _close_entry(self, entry: CacheEntry) -> None:
        """Close a backend session when the reaper evicts it."""
        await self.backend.close(entry.handle)

    async def start(self) -> None:
        from redfish_mcp.kvm.daemon.preflight import check_runtime_deps

        check_runtime_deps(self.config.backend)
        self.config.socket_dir.mkdir(parents=True, exist_ok=True)
        if self.lifecycle.socket_path.exists():
            self.lifecycle.socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.lifecycle.socket_path)
        )
        os.chmod(self.lifecycle.socket_path, 0o600)
        self.lifecycle.write_pid(os.getpid())
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        logger.info("kvm daemon listening on %s", self.lifecycle.socket_path)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("server not started")
        try:
            await self._stopping.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stopping.set()
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.lifecycle.clear()

    async def _reaper_loop(self) -> None:
        try:
            while not self._stopping.is_set():
                await self.reaper.tick()
                if self.reaper.should_exit():
                    self._stopping.set()
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message(line)
                except ProtocolError as exc:
                    logger.warning("protocol error: %s", exc)
                    continue
                if not isinstance(msg, Request):
                    continue
                resp: Response = await self.router.dispatch(msg)
                writer.write(encode_message(resp))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = KVMConfig.load()
    server = DaemonServer(cfg)
    await server.start()
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
