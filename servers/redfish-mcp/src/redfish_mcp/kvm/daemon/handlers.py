"""Router handler registration for the KVM daemon.

Phase 2 registers two methods: "open" and "screen". Other methods (sendkey,
sendkeys, type_and_read, close, status) stay unregistered and return
method_not_found until phase 3 fills them in.

The FakeBackend tests use backend="fake" as the session-cache key; the
Java backend uses backend="java". The key is derived from the backend
instance's _backend_name attribute if present, else from the backend
class name.
"""

from __future__ import annotations

import base64
from typing import Any

from redfish_mcp.kvm.backend import KVMBackend
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.daemon.session_ops import (
    default_open_timeout_s,
    default_screenshot_timeout_s,
    open_session,
    screenshot_session,
)


def _backend_name_for(backend: KVMBackend) -> str:
    """Best-effort backend identifier for session cache keying.

    Java backend returns session handles with backend="java" and is keyed
    "java" in the cache. FakeBackend (tests only) opens with backend="fake"
    — we have to key it the same way so test roundtrips work.
    """
    cls_name = type(backend).__name__.lower()
    if "fake" in cls_name:
        return "fake"
    if "java" in cls_name:
        return "java"
    return cls_name


def register_kvm_handlers(
    *,
    router: Router,
    cache: SessionCache,
    progress: ProgressPublisher,
    backend: KVMBackend,
) -> None:
    """Register `open` and `screen` handlers on the router."""

    backend_name = _backend_name_for(backend)

    def _session_key(host: str, user: str) -> str:
        return f"{backend_name}:{host}:{user}"

    async def handle_open(params: dict[str, Any]) -> dict[str, Any]:
        host = params["host"]
        user = params["user"]
        password = params["password"]
        timeout_s = float(params.get("timeout_s") or default_open_timeout_s())
        session_key = _session_key(host, user)

        existing = cache.get(host, user, backend_name)
        if existing is not None:
            h = existing.handle
            return {
                "session_id": h.session_id,
                "host": h.host,
                "user": h.user,
                "backend": h.backend,
                "opened_at_ms": h.opened_at_ms,
                "from_cache": True,
            }

        handle = await open_session(
            backend=backend,
            progress=progress,
            host=host,
            user=user,
            password=password,
            session_key=session_key,
            timeout_s=timeout_s,
        )
        cache.put(host, user, backend_name, handle)
        return {
            "session_id": handle.session_id,
            "host": handle.host,
            "user": handle.user,
            "backend": handle.backend,
            "opened_at_ms": handle.opened_at_ms,
            "from_cache": False,
        }

    async def handle_screen(params: dict[str, Any]) -> dict[str, Any]:
        host = params["host"]
        user = params["user"]
        password = params["password"]
        timeout_s = float(params.get("timeout_s") or default_screenshot_timeout_s())

        entry = cache.get(host, user, backend_name)
        if entry is None:
            session_key = _session_key(host, user)
            handle = await open_session(
                backend=backend,
                progress=progress,
                host=host,
                user=user,
                password=password,
                session_key=session_key,
            )
            entry = cache.put(host, user, backend_name, handle)

        png_bytes = await screenshot_session(
            backend=backend, session=entry.handle, timeout_s=timeout_s
        )

        return {
            "mode": "image",
            "png_b64": base64.b64encode(png_bytes).decode("ascii"),
            "session_id": entry.handle.session_id,
        }

    router.register("open", handle_open)
    router.register("screen", handle_screen)
