"""End-to-end tests for JavaIkvmBackend against a real Supermicro BMC.

Gating:
    REDFISH_KVM_E2E=1           (required)
    REDFISH_IP=<bmc-ip>         (required; default 192.168.196.1 if absent)
    REDFISH_USER=<username>     (required)
    REDFISH_PASSWORD=<password> (required)

Run:
    REDFISH_KVM_E2E=1 REDFISH_IP=192.168.196.1 \\
        REDFISH_USER=ADMIN REDFISH_PASSWORD=xxx \\
        uv run pytest tests/kvm/test_java_backend_e2e.py -v
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from redfish_mcp.kvm.backend import ProgressEvent
from redfish_mcp.kvm.backends.java import JavaIkvmBackend
from redfish_mcp.kvm.exceptions import AuthFailedError

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("REDFISH_KVM_E2E") != "1",
        reason="REDFISH_KVM_E2E=1 not set",
    ),
    pytest.mark.skipif(
        not (os.getenv("REDFISH_USER") and os.getenv("REDFISH_PASSWORD")),
        reason="REDFISH_USER/REDFISH_PASSWORD not set",
    ),
]


def _host() -> str:
    return os.getenv("REDFISH_IP", "192.168.196.1")


def _creds() -> tuple[str, str]:
    return os.getenv("REDFISH_USER", ""), os.getenv("REDFISH_PASSWORD", "")


@pytest.fixture
def backend(tmp_path: Path) -> JavaIkvmBackend:
    return JavaIkvmBackend(jar_cache_root=tmp_path / "jars", java_bin="java")


@pytest.mark.anyio
async def test_open_screenshot_close_happy_path(backend: JavaIkvmBackend):
    events: list[ProgressEvent] = []

    async def progress(e):
        events.append(e)

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        assert handle.backend == "java"
        assert handle.host == _host()
        png = await backend.screenshot(handle)
        assert png.startswith(b"\x89PNG")
        assert len(png) > 1024
    finally:
        await backend.close(handle)

    stages = [e.stage for e in events]
    assert stages[0] == "authenticating"
    assert stages[-1] == "ready"


@pytest.mark.anyio
async def test_screenshot_returns_valid_png_dimensions(backend: JavaIkvmBackend):
    import io

    from PIL import Image

    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        png = await backend.screenshot(handle)
        img = Image.open(io.BytesIO(png))
        assert img.width >= 640
        assert img.height >= 480
    finally:
        await backend.close(handle)


@pytest.mark.anyio
async def test_bad_credentials_raise_auth_failed(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    with pytest.raises(AuthFailedError):
        await backend.open(_host(), "NOT-A-REAL-USER", "definitely-wrong", progress)


@pytest.mark.anyio
async def test_session_survives_idle_time(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        time.sleep(5)
        png = await backend.screenshot(handle)
        assert png.startswith(b"\x89PNG")
    finally:
        await backend.close(handle)


@pytest.mark.anyio
async def test_health_reports_ok(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        assert await backend.health(handle) == "ok"
    finally:
        await backend.close(handle)


@pytest.fixture
def anyio_backend():
    return "asyncio"
