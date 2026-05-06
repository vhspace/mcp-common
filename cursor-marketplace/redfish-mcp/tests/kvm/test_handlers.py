"""Tests for daemon request handlers."""

from __future__ import annotations

import base64

import pytest

from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.handlers import register_kvm_handlers
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.fake_backend import FakeBackend
from redfish_mcp.kvm.protocol import Request


@pytest.mark.anyio
async def test_open_handler_produces_result():
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(router=router, cache=cache, progress=publisher, backend=backend)
    resp = await router.dispatch(
        Request(
            id=1,
            method="open",
            params={"host": "10.0.0.1", "user": "admin", "password": "p"},
        )
    )
    assert resp.result is not None
    assert "session_id" in resp.result
    assert resp.result["host"] == "10.0.0.1"
    assert resp.result["backend"] == "fake"


@pytest.mark.anyio
async def test_screen_handler_returns_png_b64():
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(router=router, cache=cache, progress=publisher, backend=backend)
    # Open first.
    await router.dispatch(
        Request(
            id=1,
            method="open",
            params={"host": "10.0.0.1", "user": "admin", "password": "p"},
        )
    )

    screen_resp = await router.dispatch(
        Request(
            id=2,
            method="screen",
            params={
                "host": "10.0.0.1",
                "user": "admin",
                "password": "p",
                "mode": "image",
            },
        )
    )
    assert screen_resp.result is not None
    png_b64 = screen_resp.result["png_b64"]
    png_bytes = base64.b64decode(png_b64)
    assert png_bytes.startswith(b"\x89PNG")


@pytest.mark.anyio
async def test_unknown_host_in_screen_reopens():
    """screen against a host not in cache triggers an implicit open."""
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(router=router, cache=cache, progress=publisher, backend=backend)
    resp = await router.dispatch(
        Request(
            id=1,
            method="screen",
            params={
                "host": "10.0.0.1",
                "user": "admin",
                "password": "p",
                "mode": "image",
            },
        )
    )
    assert resp.result is not None
    assert resp.result["png_b64"] != ""


@pytest.fixture
def anyio_backend():
    return "asyncio"
