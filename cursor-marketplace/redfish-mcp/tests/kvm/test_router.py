"""Tests for the request router."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.exceptions import AuthFailedError
from redfish_mcp.kvm.protocol import Request, Response


class TestRouter:
    @pytest.mark.anyio
    async def test_dispatch_calls_registered_handler(self):
        r = Router()

        async def handle_echo(params):
            return {"echoed": params.get("value")}

        r.register("echo", handle_echo)
        resp = await r.dispatch(Request(id=1, method="echo", params={"value": 42}))
        assert isinstance(resp, Response)
        assert resp.id == 1
        assert resp.result == {"echoed": 42}
        assert resp.error is None

    @pytest.mark.anyio
    async def test_unknown_method_returns_error(self):
        r = Router()
        resp = await r.dispatch(Request(id=7, method="nope"))
        assert resp.error is not None
        assert resp.error.code == "method_not_found"
        assert resp.id == 7

    @pytest.mark.anyio
    async def test_kvm_error_in_handler_is_mapped(self):
        r = Router()

        async def bad(_params):
            raise AuthFailedError("bad", stage="authenticating")

        r.register("bad", bad)
        resp = await r.dispatch(Request(id=9, method="bad"))
        assert resp.error is not None
        assert resp.error.code == "auth_failed"
        assert resp.error.stage == "authenticating"

    @pytest.mark.anyio
    async def test_unexpected_exception_is_mapped_to_internal(self):
        r = Router()

        async def boom(_params):
            raise RuntimeError("surprise")

        r.register("boom", boom)
        resp = await r.dispatch(Request(id=11, method="boom"))
        assert resp.error is not None
        assert resp.error.code == "internal"
        assert "surprise" in resp.error.message


@pytest.fixture
def anyio_backend():
    return "asyncio"
