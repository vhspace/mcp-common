"""Tests for MCP tool stubs."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.tools import (
    kvm_close,
    kvm_screen,
    kvm_sendkey,
    kvm_sendkeys,
    kvm_status,
    kvm_type_and_read,
)


class TestToolStubs:
    @pytest.mark.anyio
    async def test_screen_calls_daemon_client(self, monkeypatch: pytest.MonkeyPatch):
        """kvm_screen delegates to DaemonClient.request."""
        called: dict = {}

        async def fake_ensure(*_args, **_kwargs):
            return None

        async def fake_request(self, method, params=None, **_kwargs):
            called["method"] = method
            called["params"] = params
            return {
                "mode": "image",
                "png_b64": "ZmFrZQ==",
                "session_id": "s1",
            }

        class FakeDaemonLifecycle:
            def __init__(self, cfg):
                self.socket_path = "/tmp/fake.sock"

        class FakeKVMConfig:
            @staticmethod
            def load():
                return FakeKVMConfig()

        class FakeDaemonClient:
            def __init__(self, socket_path):
                self.socket_path = socket_path

            async def request(self, method, params=None, **_kwargs):
                called["method"] = method
                called["params"] = params
                return {
                    "mode": "image",
                    "png_b64": "ZmFrZQ==",
                    "session_id": "s1",
                }

        monkeypatch.setattr("redfish_mcp.kvm.autostart.ensure_daemon_running", fake_ensure)
        monkeypatch.setattr("redfish_mcp.kvm.config.KVMConfig", FakeKVMConfig)
        monkeypatch.setattr("redfish_mcp.kvm.daemon.lifecycle.DaemonLifecycle", FakeDaemonLifecycle)
        monkeypatch.setattr("redfish_mcp.kvm.client.DaemonClient", FakeDaemonClient)

        result = await kvm_screen(host="10.0.0.1", user="u", password="p", mode="image")
        assert result["ok"] is True
        assert result["mode"] == "image"
        assert result["png_b64"] == "ZmFrZQ=="
        assert called["method"] == "screen"
        assert called["params"]["host"] == "10.0.0.1"
        assert called["params"]["mode"] == "image"

    @pytest.mark.anyio
    async def test_sendkey_returns_not_implemented(self):
        result = await kvm_sendkey(host="h", user="u", password="p", key="Enter")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_sendkeys_returns_not_implemented(self):
        result = await kvm_sendkeys(host="h", user="u", password="p", text="hi")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_type_and_read_returns_not_implemented(self):
        result = await kvm_type_and_read(host="h", user="u", password="p", keys="a")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_close_returns_not_implemented(self):
        result = await kvm_close(host="h", user="u", password="p")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_status_returns_not_implemented(self):
        result = await kvm_status()
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}


@pytest.fixture
def anyio_backend():
    return "asyncio"
