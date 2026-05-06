"""Tests for the _vnc wrapper (post-spike: vncdotool)."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.backends._subprocess import SessionSubprocesses
from redfish_mcp.kvm.backends._vnc import VncSession, connect, screenshot

pytestmark = pytest.mark.subprocess


@pytest.mark.anyio
async def test_connect_and_screenshot_returns_png():
    async with SessionSubprocesses.for_x11_only(geometry="640x480x24") as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        session = await connect("127.0.0.1", spawned.vnc_port, password)
        try:
            assert isinstance(session, VncSession)
            png = await screenshot(session)
            assert png.startswith(b"\x89PNG")
            assert 500 < len(png) < 1_000_000
        finally:
            await session.close()


@pytest.mark.anyio
async def test_sendkey_raises_not_implemented():
    async with SessionSubprocesses.for_x11_only(geometry="320x240x24") as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        session = await connect("127.0.0.1", spawned.vnc_port, password)
        try:
            from redfish_mcp.kvm.backends._vnc import sendkey

            with pytest.raises(NotImplementedError):
                await sendkey(session, "Enter")
        finally:
            await session.close()
